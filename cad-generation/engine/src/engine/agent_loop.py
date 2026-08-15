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
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from pydantic import BaseModel

from engine.catalogue import CATALOGUES
from engine.evaluate import EvaluationReport, evaluate
from engine.geometry.registry import generators
from engine.ir import RobotIR, Revision

RejectHook = Callable[[int, str], None]

TOOL_NAME = "propose_robot_design"
# The local OpenAI-compatible endpoint. Port 8100, not 8000: on this machine
# :8000 is a *mock* OpenAI server belonging to the pcb-ai project, which happily
# returns plausible completions for a model it does not have. Defaulting there
# meant a design run could "succeed" against a stub and report a robot nobody
# generated — the worst failure mode this loop has, because it looks like success.
LOCAL_DEFAULT_BASE_URL = "http://localhost:8100/v1"
LOCAL_DEFAULT_MODEL = "qwen3.8-27b"
CLAUDE_DEFAULT_MODEL = "claude-opus-5"
# Thinking counts against this too on opus-5 — see the call site in _run_claude.
CLAUDE_MAX_TOKENS = 16000

# Back-compat aliases: these names are referenced elsewhere in the tree.
LAGUNA_DEFAULT_BASE_URL = LOCAL_DEFAULT_BASE_URL
LAGUNA_DEFAULT_MODEL = LOCAL_DEFAULT_MODEL

# A constrained proposal is a few thousand tokens at the ~15 tok/s the GB10 gets
# on an NVFP4 Laguna, so a single call can legitimately run several minutes. The
# OpenAI SDK's 600s default read timeout cuts that off mid-answer and its retry
# throws away every token, so the loop can spin forever without ever finishing
# one proposal. Bound it by "the server has actually stalled" instead.
LAGUNA_REQUEST_TIMEOUT_S = 1800.0


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

Connectivity is checked before any criterion runs: every link must be reachable \
from `root_link` by following joints, and every non-root link has exactly one \
parent joint. A link you add but never attach — a battery, a payload, an \
electronics box, a sensor mast — makes the whole proposal invalid, so give every \
link its joint. Use kind="fixed" for anything that doesn't move; only wheels and \
actual articulation need "revolute". If you cannot justify a joint for a link, \
leave the link out.

Available geometry generators: {generator_list}
  tube params (meters): outer_diameter, inner_diameter, length
  plate params (meters): length, width, thickness
  bracket params (meters): arm_a_length, arm_b_length, thickness, width

Available catalogues (use only these keys — inventing a key is not allowed):
{catalogue_lines}

Rules, non-negotiable:
- Never a bare number. Every value is either a Quantity or a CatalogueParam, and \
which one is not a choice:
  - Geometry params and joint limits are ALWAYS Quantity — a dimension is measured, \
not purchased. `"length": {{"value": 0.30, "unit": "m", "provenance": \
{{"status": "ASSUMED", "source": "", "note": "30cm chassis per the intent"}}}}`. \
A CatalogueParam here is rejected before evaluation: there is no catalogue of lengths.
  - CatalogueParam is ONLY for `material` on a link and `actuator` on a joint, and its \
`value` must be a key listed above. `{{"kind": "catalogue", "value": "aluminum_6061", \
"catalogue": "materials"}}`.
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
    author: str,
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
        # Which model actually authored this revision, not which one we hoped
        # would. Revisions are the permanent record (§2, §11.8) and §7 scores
        # prediction accuracy per agent to decide "whether a local model is good
        # enough for any given agent — no assumption, measure it". Stamping every
        # revision `agent:claude` while Laguna wrote it makes that measurement
        # read the wrong model's name.
        author=author,
        rationale=proposal.rationale,
        # The design itself. Required by Revision, and the whole point of the
        # record — a revision without its IR is an audit trail of nothing.
        ir=proposal.robot,
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
    on_reject: RejectHook | None = None,
) -> list[LoopStep]:
    """OpenAI-compatible chat-completions path — targets vLLM serving
    poolside/Laguna-S-2.1-NVFP4 locally on the Spark (setup/serve_laguna.sh),
    or any other OpenAI-compatible server pointed at by `base_url`.
    """
    import openai

    client = openai.OpenAI(base_url=base_url, api_key=os.environ.get("OPENAI_API_KEY", "not-needed"))

    # Structured output, not tool calling. `tool_choice={"type": "function", ...}`
    # reads like a constraint but vLLM serving a reasoning model treats it as a
    # hint: generation stays unconstrained and `--tool-call-parser` only scrapes a
    # tool call out of the text afterwards. Laguna spends that freedom thinking —
    # thousands of tokens of "Let me think through this carefully" — and never
    # reaches the call, so every request ran to the context limit and produced
    # nothing. A json_schema response_format constrains decoding itself, so the
    # first token is already `{` and the answer is exactly the Proposal schema.
    # The Anthropic path below keeps real tool use, which does constrain there.
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "proposal", "schema": _tool_schema()},
    }
    messages: list[dict] = [
        {"role": "system", "content": _system_prompt(max_tier)},
        {
            "role": "user",
            "content": (
                f"Design intent: {intent}\n\nPropose an initial design. "
                "Reply with the JSON object only."
            ),
        },
    ]

    steps: list[LoopStep] = []
    design_id = uuid4()
    parent_id: UUID | None = None

    for iteration in range(max_iterations):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format=response_format,
            timeout=LAGUNA_REQUEST_TIMEOUT_S,
        )
        choice = response.choices[0]
        # A reasoning model can still emit a stray end-of-thought marker before the
        # constrained span; strip it rather than failing to parse a good answer.
        raw = (choice.message.content or "").strip().removeprefix("</think>").strip()
        if choice.finish_reason == "length":
            raise RuntimeError(
                f"{base_url} hit the context limit mid-proposal ({len(raw)} chars). "
                "Serve the model with a larger --max-model-len, or lower max_iterations "
                "so the conversation history stays shorter."
            )
        if not raw:
            # A server that ignores response_format leaves the loop with nothing to
            # validate. That is a capability fact about the endpoint, not a bad
            # answer it can revise its way out of, so say which and stop rather than
            # dying on a JSONDecodeError several frames away.
            raise RuntimeError(
                f"{base_url} returned an empty completion for response_format=json_schema. "
                "The endpoint does not honour structured outputs: serve it with vLLM "
                "(>=0.6) or another server that supports json_schema response formats."
            )
        if iteration == 0 and not raw.startswith("{"):
            # Grammar-constrained decoding cannot produce a first token that isn't
            # `{`. Prose here means the server accepted `response_format` and
            # ignored it — llama.cpp does this, and so does vLLM built without a
            # guided-decoding backend. That is a capability fact about the
            # endpoint, not a bad answer it can revise its way out of, so name it
            # instead of spending every iteration re-asking a server that will
            # never comply and returning an empty list at the end.
            raise RuntimeError(
                f"{base_url} ignored response_format=json_schema and replied with prose, "
                f"not the Proposal object. Serve the model with a server that honours "
                f"structured outputs (vLLM >=0.6 with guided decoding). It said: {raw[:200]!r}"
            )
        messages.append({"role": "assistant", "content": raw})

        try:
            arguments = json.loads(raw)
            step, parent_id = _finalize_step(
                arguments, max_tier=max_tier, iteration=iteration, design_id=design_id,
                parent_id=parent_id, author=f"agent:{model}",
            )
        except (_ProposalRejected, json.JSONDecodeError) as exc:
            if on_reject is not None:
                on_reject(iteration, str(exc))
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your proposal was rejected before evaluation: {exc}\n\n"
                        "Fix exactly that and reply with the corrected JSON object only."
                    ),
                }
            )
            continue

        steps.append(step)
        if step.report.passed:
            break

        messages.append(
            {
                "role": "user",
                "content": (
                    f"{_feedback_text(step.report, step.prediction_correct)}\n\n"
                    "Revise the design to fix the failures above and reply with the "
                    "corrected JSON object only."
                ),
            }
        )

    return steps


def _run_claude(
    intent: str,
    *,
    max_tier: int,
    max_iterations: int,
    model: str,
    on_reject: RejectHook | None = None,
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
            # On claude-opus-5 thinking is on by default, and max_tokens caps
            # thinking *plus* the tool call together. 8000 was sized for a bare
            # RobotIR on a model that didn't think: a nine-link rover's proposal
            # plus its reasoning does not fit, and the truncation arrives as a
            # missing tool_use block — read below as "the model didn't answer"
            # rather than "the budget was too small". Still under the SDK's
            # non-streaming timeout, so no streaming rework is needed.
            max_tokens=CLAUDE_MAX_TOKENS,
            # A design proposal is exactly the intelligence-sensitive work the
            # high tier exists for. Named rather than left implicit because it
            # is the first knob to turn if these runs cost too much.
            output_config={"effort": "high"},
            system=_system_prompt(max_tier),
            tools=[tool],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=messages,
        )
        # A truncated turn has no tool_use block at all. `next()` would raise a
        # bare StopIteration here, which reads as a loop bug rather than a token
        # budget one — the same class of misdiagnosis the local path hit with
        # its empty completions.
        tool_use = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_use is None:
            raise RuntimeError(
                f"{model} returned no tool call (stop_reason={response.stop_reason!r}). "
                + (
                    f"The {CLAUDE_MAX_TOKENS} token budget ran out mid-proposal — raise "
                    "CLAUDE_MAX_TOKENS, or lower output_config effort so less of it goes "
                    "to thinking."
                    if response.stop_reason == "max_tokens"
                    else "Expected a forced tool call and got none."
                )
            )
        messages.append({"role": "assistant", "content": response.content})

        try:
            step, parent_id = _finalize_step(
                tool_use.input, max_tier=max_tier, iteration=iteration, design_id=design_id,
                parent_id=parent_id, author=f"agent:{model}",
            )
        except _ProposalRejected as exc:
            if on_reject is not None:
                on_reject(iteration, str(exc))
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
    on_reject: RejectHook | None = None,
) -> list[LoopStep]:
    """Pure orchestration: propose -> evaluate -> (revise -> evaluate)*.

    Stops at the first passing design or after `max_iterations`. Every
    intermediate design is recorded as an immutable Revision (§2, §11.8) —
    nothing here mutates a prior RobotIR, each attempt is a new one.

    `on_reject(iteration, reason)` is called whenever a proposal is thrown out
    before evaluation. Without it a run where every proposal is malformed is
    indistinguishable from one where the model never answered: the caller sees
    an empty list either way, having burned every iteration. The engine stays
    zero-I/O (§11.7) — the hook reports, the caller decides where to (§11.6:
    "report plainly when they're wrong").

    `provider` selects the model backend: "laguna" (default) targets the
    locally-hosted poolside/Laguna-S-2.1 vLLM server on the Spark; "claude"
    targets the Anthropic API — the fallback this team uses during overnight
    training windows when Laguna is stopped to free GPU memory.
    """
    # "qwen", "laguna", "local" and "openai" all mean the same thing: talk to a
    # local OpenAI-compatible server. The provider names a *transport*, not a
    # model — which model answers is decided by `model`/LOCAL_MODEL, so swapping
    # the served model is config, not a code change (§3 "provider-agnostic
    # wrapper... per-agent provider is config, not code").
    if provider in ("qwen", "laguna", "local", "openai"):
        return _run_laguna(
            intent,
            max_tier=max_tier,
            max_iterations=max_iterations,
            model=model or os.environ.get("LOCAL_MODEL", os.environ.get("LAGUNA_MODEL", LOCAL_DEFAULT_MODEL)),
            base_url=base_url
            or os.environ.get("LOCAL_BASE_URL", os.environ.get("LAGUNA_BASE_URL", LOCAL_DEFAULT_BASE_URL)),
            on_reject=on_reject,
        )
    if provider == "claude":
        return _run_claude(
            intent,
            max_tier=max_tier,
            max_iterations=max_iterations,
            model=model or CLAUDE_DEFAULT_MODEL,
            on_reject=on_reject,
        )
    raise ValueError(f"unknown provider {provider!r}; expected 'laguna' or 'claude'")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m engine.agent_loop")
    parser.add_argument("intent", help="text description of the robot to design")
    parser.add_argument("--max-tier", type=int, default=0)
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument("--provider", choices=["qwen", "laguna", "local", "openai", "claude"], default="qwen")
    parser.add_argument("--model", default=None, help="overrides the provider's default model/served-name")
    parser.add_argument("--base-url", default=None, help="local providers only; default http://localhost:8100/v1")
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
