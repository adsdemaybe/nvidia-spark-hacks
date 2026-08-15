"""
Multi-agent design refinement as a LangGraph state machine.

    evaluate ──> analyst ──> proposer ──> critic ──┐
       ^                        ^                  │ revise (bounded)
       │                        └──────────────────┤
       │                                           │ accept
       └──────────── verify (deterministic) <──────┘

The division of labour is the point:

    AGENTS   diagnose, propose, and critique. They reason about mechanism and
             trade-offs, which is what they are good at.
    HARNESS  builds the CAD, runs the physics, scores the criteria, and decides
             whether a proposal is kept. It is deterministic and cannot be
             argued with.

An agent can propose anything; it can never declare success. `verify` re-runs
the real evaluation and rejects a proposal that does not improve the score, so
a confidently-wrong agent costs one evaluation and nothing else.

Runs without any API key: if no provider has credentials, deterministic
heuristic agents stand in, so the graph itself stays testable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

import design_loop as D
import rover_arm as R
from ai import ProviderError, available, build_structured, spec_for
from ai.schemas import Critique, Diagnosis, Proposal, RunSummary

SKILLS = Path(__file__).resolve().parent.parent / "ai" / "skills"
MAX_ROUNDS = int(os.environ.get("ROVER_MAX_ROUNDS", "8"))
MAX_REVISIONS = 2          # critic <-> proposer exchanges before forcing a run


def _skill(name: str) -> str:
    return (SKILLS / f"{name}.md").read_text()


def _keep_last(_old, new):
    return new


class DesignState(TypedDict, total=False):
    task: str
    design: dict[str, Any]
    report: dict[str, Any]
    best_score: float
    best_design: dict[str, Any]
    best_payload: float
    diagnosis: Annotated[dict, _keep_last]
    proposal: Annotated[dict, _keep_last]
    critique: Annotated[dict, _keep_last]
    history: list[dict]
    round: int
    revisions: int
    workdir: str
    use_llm: bool
    summary: dict


# =============================================================================
# Harness nodes — deterministic, the source of truth
# =============================================================================

def node_evaluate(state: DesignState) -> DesignState:
    """Run the real CAD + physics evaluation on the current design."""
    rep = D.evaluate(dict(state["design"]), state["workdir"])
    report = {
        "passed": rep.passed,
        "score": round(rep.score, 4),
        "payload_kg": round(rep.payload, 4),
        "failing": rep.failing(),
        "checks": [{"name": c.name, "ok": c.ok, "value": round(c.value, 4),
                    "target": round(c.target, 4), "note": c.note}
                   for c in rep.checks],
    }
    best = state.get("best_score")
    out: DesignState = {"report": report}
    if best is None or report["score"] < best:
        out |= {"best_score": report["score"],
                "best_design": dict(state["design"]),
                "best_payload": report["payload_kg"]}
    return out


def node_verify(state: DesignState) -> DesignState:
    """
    Apply the accepted proposal, re-evaluate, and keep it only if it improved.

    This is the gate the agents cannot talk their way past.
    """
    proposal = state.get("proposal") or {}
    candidate = dict(state["design"])
    applied, rejected = [], []

    for ch in proposal.get("changes", []):
        var, val = ch.get("variable"), ch.get("value")
        if var in ("SHOULDER_MOTOR", "DRIVE_MOTOR"):
            if val in R.MOTORS:
                candidate[var] = val
                applied.append(f"{var}={val}")
            else:
                rejected.append(f"{var}={val!r} is not in the catalogue")
            continue
        if var == "SHOULDER_GEAR":
            if float(val) in R.GEAR_OPTIONS:
                candidate[var] = float(val)
                applied.append(f"{var}={val}")
            else:
                rejected.append(f"{val}:1 is not a purchasable ratio")
            continue
        if var in R.DESIGN_VARS:
            lo, hi = R.DESIGN_VARS[var]
            clamped = min(hi, max(lo, float(val)))
            if abs(clamped - float(val)) > 1e-9:
                rejected.append(f"{var} clamped {val} -> {clamped}")
            candidate[var] = clamped
            applied.append(f"{var}={clamped:g}")
        else:
            rejected.append(f"{var!r} is not a design variable")

    rep = D.evaluate(candidate, state["workdir"])
    improved = rep.score < state["report"]["score"] - 1e-9
    equal_but_better = (rep.score <= 1e-9
                        and state["report"]["score"] <= 1e-9
                        and rep.payload > state["report"]["payload_kg"] + 1e-4)
    accept = improved or equal_but_better

    entry = {
        "round": state.get("round", 0),
        "applied": applied,
        "rejected_by_harness": rejected,
        "score_before": state["report"]["score"],
        "score_after": round(rep.score, 4),
        "payload_after_g": round(rep.payload * 1000, 1),
        "accepted": accept,
        "failing": rep.failing(),
    }

    out: DesignState = {
        "history": state.get("history", []) + [entry],
        "round": state.get("round", 0) + 1,
        "revisions": 0,
    }
    if accept:
        out["design"] = candidate
        out["report"] = {
            "passed": rep.passed, "score": round(rep.score, 4),
            "payload_kg": round(rep.payload, 4), "failing": rep.failing(),
            "checks": [{"name": c.name, "ok": c.ok, "value": round(c.value, 4),
                        "target": round(c.target, 4), "note": c.note}
                       for c in rep.checks],
        }
        if rep.score < state.get("best_score", float("inf")):
            out |= {"best_score": round(rep.score, 4),
                    "best_design": dict(candidate),
                    "best_payload": round(rep.payload, 4)}
    return out


# =============================================================================
# Agent nodes
# =============================================================================

def _ctx(state: DesignState) -> str:
    return json.dumps({
        "task": state["task"],
        "current_design": state["design"],
        "report": state["report"],
        "design_variable_bounds": {k: list(v) for k, v in R.DESIGN_VARS.items()},
        "motor_catalogue": R.MOTORS,
        "gear_options": list(R.GEAR_OPTIONS),
        "gear_backlash_deg": R.GEAR_BACKLASH_DEG,
        "history": state.get("history", [])[-4:],
    }, indent=2, default=str)


def _invoke(role: str, schema, system: str, human: str):
    model = build_structured(spec_for(role), schema)
    return model.invoke([("system", system), ("human", human)])


def node_analyst(state: DesignState) -> DesignState:
    if not state["use_llm"]:
        return {"diagnosis": _heuristic_diagnosis(state)}
    try:
        d = _invoke("analyst", Diagnosis, _skill("analyst"),
                    f"Diagnose this design's failures.\n\n{_ctx(state)}")
        return {"diagnosis": d.model_dump()}
    except (ProviderError, Exception):
        return {"diagnosis": _heuristic_diagnosis(state)}


def node_proposer(state: DesignState) -> DesignState:
    if not state["use_llm"]:
        return {"proposal": _heuristic_proposal(state)}
    crit = state.get("critique") or {}
    extra = ""
    if crit.get("verdict") in ("revise", "reject"):
        extra = (f"\n\nA critic rejected your previous proposal:\n"
                 f"{crit.get('reasoning')}\n"
                 f"Suggested revision: {crit.get('suggested_revision')}")
    try:
        p = _invoke("proposer", Proposal, _skill("proposer"),
                    f"Diagnosis:\n{json.dumps(state.get('diagnosis', {}), indent=2)}"
                    f"\n\n{_ctx(state)}{extra}")
        return {"proposal": p.model_dump()}
    except (ProviderError, Exception):
        return {"proposal": _heuristic_proposal(state)}


def node_critic(state: DesignState) -> DesignState:
    if not state["use_llm"]:
        return {"critique": _heuristic_critique(state)}
    try:
        c = _invoke("critic", Critique, _skill("critic"),
                    f"Proposal:\n{json.dumps(state.get('proposal', {}), indent=2)}"
                    f"\n\n{_ctx(state)}")
        return {"critique": c.model_dump()}
    except (ProviderError, Exception):
        return {"critique": _heuristic_critique(state)}


# =============================================================================
# Heuristic stand-ins (no API key required)
# =============================================================================

def _heuristic_diagnosis(state: DesignState) -> dict:
    failing = state["report"]["failing"]
    checks = {c["name"]: c for c in state["report"]["checks"]}
    causes = []
    if "arm_holds" in failing:
        c = checks["arm_holds"]
        ratio = c["value"] / c["target"] if c["target"] else 99
        causes.append(f"shoulder actuator undersized by {ratio:.2f}x")
    if "payload" in failing:
        causes.append("tipping geometry: gripper lever far exceeds wheelbase")
    if "backlash" in failing:
        causes.append("gearbox backlash amplified over the arm's reach")
    return {"root_cause": "; ".join(causes) or "no failing criteria",
            "blocking_criteria": failing, "ruled_out": [],
            "confidence": "medium"}


def _heuristic_proposal(state: DesignState) -> dict:
    """Deterministic fallback: the obvious lever for each failing criterion."""
    failing = state["report"]["failing"]
    checks = {c["name"]: c for c in state["report"]["checks"]}
    changes = []

    if "arm_holds" in failing:
        need = checks["arm_holds"]["value"]
        for m in R.MOTOR_BY_TORQUE:
            if R.MOTORS[m]["torque"] >= need * 1.15:
                changes.append({"variable": "SHOULDER_MOTOR", "value": m,
                                "rationale": f"catalogue motor with "
                                             f"{R.MOTORS[m]['torque']} N.m "
                                             f"covers {need:.2f} N.m + margin"})
                break
    if "payload" in failing and len(changes) < 2:
        cur = state["design"].get("AXLE_FRAC", 0.25)
        lo, hi = R.DESIGN_VARS["AXLE_FRAC"]
        changes.append({"variable": "AXLE_FRAC",
                        "value": min(hi, round(cur + 0.06, 3)),
                        "rationale": "extend the tipping fulcrum outward"})
    if "backlash" in failing:
        changes.append({"variable": "SHOULDER_GEAR", "value": 1.0,
                        "rationale": "direct drive has zero backlash"})
    if not changes:
        cur = state["design"].get("BALLAST_M", 0.0)
        changes.append({"variable": "BALLAST_M", "value": min(1.2, cur + 0.2),
                        "rationale": "rear ballast for tip-over margin"})
    return {"changes": changes[:3],
            "expected_effect": "reduce the failing criteria's shortfall",
            "risks": ["added mass may reduce payload"]}


def _heuristic_critique(state: DesignState) -> dict:
    p = state.get("proposal") or {}
    violations = []
    for ch in p.get("changes", []):
        v, val = ch.get("variable"), ch.get("value")
        if v in ("SHOULDER_MOTOR", "DRIVE_MOTOR") and val not in R.MOTORS:
            violations.append(f"{val!r} is not a catalogue motor")
        elif v == "SHOULDER_GEAR" and float(val) not in R.GEAR_OPTIONS:
            violations.append(f"{val}:1 is not a purchasable ratio")
        elif v in R.DESIGN_VARS:
            lo, hi = R.DESIGN_VARS[v]
            if not (lo <= float(val) <= hi):
                violations.append(f"{v}={val} outside [{lo}, {hi}]")
    return {"verdict": "reject" if violations else "accept",
            "reasoning": "; ".join(violations) or "buildable and in bounds",
            "violated_constraints": violations, "suggested_revision": None}


# =============================================================================
# Routing
# =============================================================================

def route_after_evaluate(state: DesignState) -> Literal["analyst", "done"]:
    if state["report"]["passed"]:
        return "done"
    if state.get("round", 0) >= MAX_ROUNDS:
        return "done"
    return "analyst"


def route_after_critic(state: DesignState) -> Literal["proposer", "verify"]:
    verdict = (state.get("critique") or {}).get("verdict", "accept")
    if verdict in ("revise", "reject") and state.get("revisions", 0) < MAX_REVISIONS:
        return "proposer"
    return "verify"


def node_bump_revision(state: DesignState) -> DesignState:
    return {"revisions": state.get("revisions", 0) + 1}


def route_after_verify(state: DesignState) -> Literal["evaluate", "done"]:
    if state.get("round", 0) >= MAX_ROUNDS:
        return "done"
    if state["report"]["passed"]:
        return "done"
    return "evaluate"


def build_graph():
    g = StateGraph(DesignState)
    g.add_node("evaluate", node_evaluate)
    g.add_node("analyst", node_analyst)
    g.add_node("proposer", node_proposer)
    g.add_node("critic", node_critic)
    g.add_node("bump", node_bump_revision)
    g.add_node("verify", node_verify)

    g.set_entry_point("evaluate")
    g.add_conditional_edges("evaluate", route_after_evaluate,
                            {"analyst": "analyst", "done": END})
    g.add_edge("analyst", "proposer")
    g.add_edge("proposer", "critic")
    g.add_conditional_edges("critic", route_after_critic,
                            {"proposer": "bump", "verify": "verify"})
    g.add_edge("bump", "proposer")
    g.add_conditional_edges("verify", route_after_verify,
                            {"evaluate": "evaluate", "done": END})
    return g.compile()


def run(task: str = "Mobile rover with a 3-axis arm that passes every "
                    "simulation criterion",
        design: dict | None = None,
        use_llm: bool | None = None,
        workdir: str | None = None) -> DesignState:
    import tempfile

    if use_llm is None:
        use_llm = any(v for k, v in available().items() if k != "ollama")

    start: DesignState = {
        "task": task,
        "design": design or _default_design(),
        "history": [], "round": 0, "revisions": 0,
        "workdir": workdir or tempfile.mkdtemp(prefix="graph_"),
        "use_llm": use_llm,
    }
    return build_graph().invoke(start, {"recursion_limit": 120})


def _default_design() -> dict:
    d = R.current_design()
    d["SHOULDER_MOTOR"] = "17HS4401"
    d["SHOULDER_GEAR"] = 1.0
    return d
