"""The single-agent design loop (§7): one designer agent plus the
deterministic evaluate/critique nodes. Phase 2 splits this into a full
LangGraph supervisor graph; Phase 1 is deliberately just this loop.

The rule at the center of the whole platform lives here in code, not just
in the doc: `evaluate()` — imported straight from the pure engine, never
reimplemented — is the only thing in this module allowed to say a design
passed. The agent's own `Proposal.predictions` are recorded and scored
*after* the harness runs, never trusted as a verdict (§7: "wrong
predictions are more informative than vague improvement").

Two providers, matching how this team actually runs the fleet (see
setup/serve_laguna.sh): during build hours, `laguna` — poolside/Laguna-S-2.1
served locally on the Spark's GB10 via vLLM's OpenAI-compatible endpoint,
free and local — and during overnight training windows, `claude`, the
Anthropic API. Both speak the same `Proposal` schema and go through the
same `evaluate()` call; only the wire format for tool calls differs.

This module is the one place in `engine` that performs network I/O — every
other module it calls into (`evaluate`, `RobotIR`, the criteria registry)
stays zero-I/O per §11 non-negotiable #7.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from pydantic import BaseModel

from engine.catalogue import CATALOGUES
from engine.evaluate import EvaluationReport, evaluate
from engine.geometry.registry import generators
from engine.ir import RobotIR, Revision

TOOL_NAME = "propose_robot_design"
LAGUNA_DEFAULT_BASE_URL = "http://localhost:8000/v1"
LAGUNA_DEFAULT_MODEL = "laguna"
CLAUDE_DEFAULT_MODEL = "claude-opus-5"


class Prediction(BaseModel):
    """A predicted outcome for one criterion, stated before evaluate() runs."""

    criterion_name: str
    predicted_passed: bool
    predicted_magnitude: float


class Proposal(BaseModel):
    """The agent's structured output. Never free text — every proposal is a
    fully-specified RobotIR the harness can evaluate as-is (§7: "Every agent
    emits a structured, Pydantic-validated schema").
    """

    rationale: str
    robot: RobotIR
    predictions: list[Prediction]


@dataclass(frozen=True)
class LoopStep:
    revision: Revision
    proposal: Proposal
    report: EvaluationReport
    # criterion name -> was the agent's pass/fail prediction correct
    prediction_correct: dict[str, bool] = field(default_factory=dict)


def _system_prompt(max_tier: int) -> str:
    generator_list = ", ".join(generators())
    catalogue_lines = "\n".join(
        f"  {name}: {', '.join(catalogue.keys())}" for name, catalogue in sorted(CATALOGUES.items())
    )
    return f"""You are the designer agent in a robot design platform. You propose robot \
designs; you never decide whether one is valid — only the harness's evaluate() \
call does that. If your prediction and the harness disagree, the harness is \
right and the question is why you were wrong.

Every proposal must be a complete, self-consistent RobotIR: a root_link, a \
list of links (each with a geometry generator + params + material), and a \
list of joints connecting them by id.

Available geometry generators: {generator_list}
  tube params (meters): outer_diameter, inner_diameter, length
  plate params (meters): length, width, thickness
  bracket params (meters): arm_a_length, arm_b_length, thickness, width

Available catalogues (use only these keys — inventing a key is not allowed):
{catalogue_lines}

Rules, non-negotiable:
- Every geometry param and joint limit is either a CatalogueParam (kind="catalogue", \
referencing a real catalogue key above) or a Quantity with value + unit + provenance. \
Never a bare number.
- provenance.status is "ASSUMED" for any value you chose yourself (source can be empty, \
but explain the choice in note); use "CONFIRMED" only if you can name a real, resolvable \
source in `source`.
- A revolute joint needs an `actuator` (a CatalogueParam from stepper_motors) if you \
want it checked against a torque budget at tier 1.
- You are being evaluated up to tier {max_tier}: tier 0 runs `static_margin` (CoM must \
stay inside the support footprint with margin) and `mount_fits` (fixed joints need real \
volumetric overlap, not two faces merely touching){"; tier 1 runs `joint_torque_budget` \
(every actuated revolute joint's static holding torque must fit its motor's stall torque)" if max_tier >= 1 else ""}.

Call `{TOOL_NAME}` with your full design, a one-paragraph rationale, and a \
prediction (pass/fail + expected magnitude) for every criterion you expect to fire on \
this design. If you're given evaluation feedback from a prior attempt, revise that \
design to fix the reported failures — don't restart from scratch unless the failure is \
structural (e.g. the topology itself is wrong)."""


def _score_predictions(predictions: list[Prediction], report: EvaluationReport) -> dict[str, bool]:
    actual_by_name = {r.name: r for r in report.results}
    scored: dict[str, bool] = {}
    for prediction in predictions:
        actual = actual_by_name.get(prediction.criterion_name)
        if actual is None:
            continue
        scored[prediction.criterion_name] = bool(prediction.predicted_passed == actual.passed)
    return scored


def _feedback_text(report: EvaluationReport, prediction_correct: dict[str, bool]) -> str:
    lines = [f"Evaluation result: {'PASS' if report.passed else 'FAIL'}"]
    lines.append(f"tiers run: {report.tiers_run}  tiers skipped: {report.tiers_skipped}")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        correctness = ""
        if r.name in prediction_correct:
            correctness = " (your prediction was correct)" if prediction_correct[r.name] else " (your prediction was WRONG)"
        lines.append(f"  [{status}] {r.name}: magnitude={r.magnitude:+.4f} {r.unit} — {r.detail}{correctness}")
    if not report.passed:
        lines.append("\nRevise the design to fix the failing criteria above. Keep what passed.")
    return "\n".join(lines)


class _ProposalRejected(Exception):
    """The model's tool call didn't parse into a valid Proposal, or the
    resulting RobotIR failed evaluate() before any criteria could run
    (unknown catalogue key, missing geometry param, etc.)."""


def _finalize_step(
    raw_arguments: dict,
    *,
    max_tier: int,
    iteration: int,
    design_id: UUID,
    parent_id: UUID | None,
) -> tuple[LoopStep, UUID]:
    try:
        proposal = Proposal.model_validate(raw_arguments)
        report = evaluate(proposal.robot, max_tier=max_tier)
    except Exception as exc:
        raise _ProposalRejected(str(exc)) from exc

    prediction_correct = _score_predictions(proposal.predictions, report)
    revision = Revision(
        design_id=design_id,
        parent_id=parent_id,
        revision_no=iteration,
        ir=proposal.robot,
        author="agent:claude",
        rationale=proposal.rationale,
    )
    step = LoopStep(revision=revision, proposal=proposal, report=report, prediction_correct=prediction_correct)
    return step, revision.id


def _tool_schema() -> dict:
    return Proposal.model_json_schema()


def _run_laguna(
    intent: str,
    *,
    max_tier: int,
    max_iterations: int,
    model: str,
    base_url: str,
) -> list[LoopStep]:
    """OpenAI-compatible chat-completions path — targets vLLM serving
    poolside/Laguna-S-2.1-NVFP4 locally on the Spark (setup/serve_laguna.sh),
    or any other OpenAI-compatible server pointed at by `base_url`.
    """
    import openai

    client = openai.OpenAI(base_url=base_url, api_key=os.environ.get("OPENAI_API_KEY", "not-needed"))
    tool = {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "Propose a complete robot design (or a revision of the previous one) as a "
                "fully-specified RobotIR, with a rationale and a predicted pass/fail + magnitude "
                "for every criterion you expect the harness to evaluate."
            ),
            "parameters": _tool_schema(),
        },
    }
    messages: list[dict] = [
        {"role": "system", "content": _system_prompt(max_tier)},
        {"role": "user", "content": f"Design intent: {intent}\n\nPropose an initial design."},
    ]

    steps: list[LoopStep] = []
    design_id = uuid4()
    parent_id: UUID | None = None

    for iteration in range(max_iterations):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
        )
        message = response.choices[0].message
        tool_call = message.tool_calls[0]
        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [tc.model_dump() for tc in message.tool_calls],
            }
        )

        try:
            arguments = json.loads(tool_call.function.arguments)
            step, parent_id = _finalize_step(
                arguments, max_tier=max_tier, iteration=iteration, design_id=design_id, parent_id=parent_id
            )
        except (_ProposalRejected, json.JSONDecodeError) as exc:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"Your proposal was rejected before evaluation: {exc}",
                }
            )
            continue

        steps.append(step)
        if step.report.passed:
            break

        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": _feedback_text(step.report, step.prediction_correct),
            }
        )

    return steps


def _run_claude(
    intent: str,
    *,
    max_tier: int,
    max_iterations: int,
    model: str,
) -> list[LoopStep]:
    import anthropic

    client = anthropic.Anthropic()
    tool = {
        "name": TOOL_NAME,
        "description": (
            "Propose a complete robot design (or a revision of the previous one) as a "
            "fully-specified RobotIR, with a rationale and a predicted pass/fail + magnitude "
            "for every criterion you expect the harness to evaluate."
        ),
        "input_schema": _tool_schema(),
    }
    messages: list[dict] = [
        {"role": "user", "content": f"Design intent: {intent}\n\nPropose an initial design."}
    ]

    steps: list[LoopStep] = []
    design_id = uuid4()
    parent_id: UUID | None = None

    for iteration in range(max_iterations):
        response = client.messages.create(
            model=model,
            max_tokens=8000,
            system=_system_prompt(max_tier),
            tools=[tool],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=messages,
        )
        tool_use = next(b for b in response.content if b.type == "tool_use")
        messages.append({"role": "assistant", "content": response.content})

        try:
            step, parent_id = _finalize_step(
                tool_use.input, max_tier=max_tier, iteration=iteration, design_id=design_id, parent_id=parent_id
            )
        except _ProposalRejected as exc:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use.id,
                            "content": f"Your proposal was rejected before evaluation: {exc}",
                            "is_error": True,
                        }
                    ],
                }
            )
            continue

        steps.append(step)
        if step.report.passed:
            break

        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": _feedback_text(step.report, step.prediction_correct),
                    }
                ],
            }
        )

    return steps


def run_design_loop(
    intent: str,
    *,
    max_tier: int = 0,
    max_iterations: int = 5,
    provider: str = "laguna",
    model: str | None = None,
    base_url: str | None = None,
) -> list[LoopStep]:
    """Pure orchestration: propose -> evaluate -> (revise -> evaluate)*.

    Stops at the first passing design or after `max_iterations`. Every
    intermediate design is recorded as an immutable Revision (§2, §11.8) —
    nothing here mutates a prior RobotIR, each attempt is a new one.

    `provider` selects the model backend: "laguna" (default) targets the
    locally-hosted poolside/Laguna-S-2.1 vLLM server on the Spark; "claude"
    targets the Anthropic API — the fallback this team uses during overnight
    training windows when Laguna is stopped to free GPU memory.
    """
    if provider == "laguna":
        return _run_laguna(
            intent,
            max_tier=max_tier,
            max_iterations=max_iterations,
            model=model or os.environ.get("LAGUNA_MODEL", LAGUNA_DEFAULT_MODEL),
            base_url=base_url or os.environ.get("LAGUNA_BASE_URL", LAGUNA_DEFAULT_BASE_URL),
        )
    if provider == "claude":
        return _run_claude(
            intent,
            max_tier=max_tier,
            max_iterations=max_iterations,
            model=model or CLAUDE_DEFAULT_MODEL,
        )
    raise ValueError(f"unknown provider {provider!r}; expected 'laguna' or 'claude'")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m engine.agent_loop")
    parser.add_argument("intent", help="text description of the robot to design")
    parser.add_argument("--max-tier", type=int, default=0)
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument("--provider", choices=["laguna", "claude"], default="laguna")
    parser.add_argument("--model", default=None, help="overrides the provider's default model/served-name")
    parser.add_argument("--base-url", default=None, help="laguna provider only; default http://localhost:8000/v1")
    args = parser.parse_args(argv)

    steps = run_design_loop(
        args.intent,
        max_tier=args.max_tier,
        max_iterations=args.max_iterations,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
    )

    for step in steps:
        print(f"--- revision {step.revision.revision_no} ---")
        print(step.proposal.rationale)
        print(_feedback_text(step.report, step.prediction_correct))
        print()

    if not steps:
        print("No revision produced a valid design.")
        return 1
    if steps[-1].report.passed:
        print(f"Converged after {len(steps)} revision(s).")
        return 0
    print(f"Did not converge within {len(steps)} revision(s).")
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
