"""
The harness, exposed as LangChain tools.

These are the agents' only route to ground truth. Every one of them is
deterministic and runs the real CAD kernel and the real physics engine — an
agent cannot fake a passing evaluation, because it never produces the verdict,
it only requests one.
"""

from __future__ import annotations

import json
import tempfile
from typing import Any

from langchain_core.tools import tool

import design_loop as D
import rover_arm as R

_WORKDIR = tempfile.mkdtemp(prefix="agent_harness_")


def _report_to_dict(rep) -> dict[str, Any]:
    return {
        "passed": rep.passed,
        "score": round(rep.score, 4),
        "payload_kg": round(rep.payload, 4),
        "checks": [
            {"name": c.name, "ok": c.ok, "value": round(c.value, 4),
             "target": round(c.target, 4), "note": c.note}
            for c in rep.checks
        ],
        "failing": rep.failing(),
    }


@tool
def evaluate_design(design: dict) -> str:
    """Build the CAD, export the URDF, run the physics sim, and score a design.

    This is ground truth. `design` maps design-variable names to values, e.g.
    {"CHASSIS_L": 210.0, "AXLE_FRAC": 0.37, "SHOULDER_MOTOR": "23HS22-2804S"}.
    Returns every criterion with its measured value and pass/fail.
    """
    try:
        rep = D.evaluate(dict(design), _WORKDIR)
    except Exception as exc:                       # noqa: BLE001
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
    return json.dumps(_report_to_dict(rep), indent=2)


@tool
def list_design_variables() -> str:
    """List every continuous design variable with its allowed [min, max] bounds.

    Proposing a value outside these bounds will be clamped by the harness.
    """
    return json.dumps({k: list(v) for k, v in R.DESIGN_VARS.items()}, indent=2)


@tool
def list_motor_catalogue() -> str:
    """List the purchasable stepper motors with datasheet specs.

    The shoulder motor must be one of these. Torque is holding torque in N.m;
    frame, bolt_pitch and pilot_d are millimetres and drive the mounting
    geometry. There is no such thing as a motor not on this list.
    """
    return json.dumps(
        {k: {kk: vv for kk, vv in spec.items()} for k, spec in R.MOTORS.items()},
        indent=2)


@tool
def list_gear_options() -> str:
    """List the purchasable planetary gear ratios and their backlash.

    Ratios are drawing-confirmed real products. A continuous ratio is NOT
    allowed — an optimiser that invents 7.5:1 has invented a part nobody sells.
    """
    return json.dumps(
        {"ratios": list(R.GEAR_OPTIONS),
         "backlash_deg": R.GEAR_BACKLASH_DEG,
         "max_end_effector_slop_mm": R.MAX_BACKLASH_MM}, indent=2)


@tool
def current_design() -> str:
    """Return the design variables currently loaded in the CAD model."""
    d = R.current_design()
    d["SHOULDER_MOTOR"] = R.SHOULDER_MOTOR
    d["DRIVE_MOTOR"] = R.DRIVE_MOTOR
    d["SHOULDER_GEAR"] = R.SHOULDER_GEAR
    return json.dumps(d, indent=2)


#: Read-only inspection tools, safe for any agent.
INSPECTION_TOOLS = [list_design_variables, list_motor_catalogue,
                    list_gear_options, current_design]

#: The full set, including the expensive ground-truth evaluation.
ALL_TOOLS = INSPECTION_TOOLS + [evaluate_design]
