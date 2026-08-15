"""`pcb-ai` -> robot: the up direction of the §7 contract.

What comes back is not an opinion. §5 puts the deterministic outputs of `pcb-ai`
— board mass from BOM and stackup, dissipation from its solvers, DRC/DFM status
— in the **MEASURED** class: "computed by an independent gated pipeline from the
routed artifact, not asserted by any agent". So this module never adjusts,
averages, or second-guesses a number it reads. It converts units, attaches
provenance, and puts the value where the robot's criteria will find it.

The one thing it does compute is harness length, and only because neither side
can: the board knows where its connector is, the robot knows where the motor is,
and the distance between them exists only once both are on the table. §7.3 —
"harness lengths stop being guesses" — is that calculation, and it feeds the
voltage-drop term the §3 actuator model needs.

Revisions are immutable (§12 #8), so every function here returns a **new**
`RobotIR`. Ingesting board facts is a revision of the design, not an edit to it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from engine.ir import Provenance, Quantity, RobotIR, Vec3
from engine.kinematics import link_frames, link_geometry_transform


class GateFailure(RuntimeError):
    """A `pcb-ai` hard gate failed, and no robot-side caller may waive it.

    §12 non-negotiable #10: "A board gate failure is a robot failure. No
    robot-side agent — chief included — can waive a `pcb-ai` hard failure." The
    exception exists so that waiving one requires writing code that catches it,
    which is a reviewable act, rather than ignoring a boolean, which is not.
    """


@dataclass(frozen=True)
class BoardFacts:
    """What one `pcb-ai` run measured, in the robot's units."""

    board_id: str
    run_dir: str
    gate_status: str  # "PASS" | "FAIL"
    mass_kg: float | None
    com_mm: tuple[float, float] | None
    dissipation_w: float
    outline_mm: tuple[float, float]
    max_component_height_mm: float
    connectors: dict[str, tuple[float, float]]  # ref -> (x_mm, y_mm) in board frame
    gate_detail: str


def _measured(run_dir: str, note: str) -> Provenance:
    return Provenance(status="MEASURED", source=run_dir, note=note)


def gate_status(summary: dict) -> tuple[str, str]:
    """PASS/FAIL for a run, and the sentence explaining it.

    Read from the run's own summary rather than recomputed. `accepted` is
    `pcb-ai`'s verdict and it already accounts for its whole ladder; the counts
    are unpacked alongside it so a robot-side report can say *which* gate failed
    without the robot side owning any opinion about gates.
    """
    iterations = summary.get("iterations") or []
    if not iterations:
        return "FAIL", "the run recorded no iterations — it did not complete"
    last = iterations[-1]

    hard = int(last.get("hard_failures") or 0)
    drc = int(last.get("drc_errors") or 0)
    dfm = int(last.get("dfm_errors") or 0)
    compiled = bool(last.get("compiled"))
    accepted = bool(summary.get("accepted"))

    if not compiled:
        return "FAIL", "the board did not compile"
    reasons = []
    if hard:
        reasons.append(f"{hard} hard failure(s)")
    if drc:
        reasons.append(f"{drc} DRC error(s)")
    if dfm:
        reasons.append(f"{dfm} DFM error(s)")
    if reasons:
        return "FAIL", "; ".join(reasons)
    if not accepted:
        return (
            "FAIL",
            "every counted gate passed but the run was not accepted — a review "
            "finding or a budget stop, and the run's own verdict is authoritative",
        )
    return "PASS", "all gates passed and the run was accepted"


def board_facts(
    board_id: str, board_report: dict, summary: dict, run_dir: str
) -> BoardFacts:
    """Pure: two parsed JSON documents -> the facts the robot IR wants."""
    status, detail = gate_status(summary)

    points = (board_report.get("outline_mm") or {}).get("points") or []
    if len(points) < 3:
        raise ValueError(
            f"board report for {board_id!r} has no usable outline "
            f"({len(points)} points); the enclosure and the fit check both need one"
        )
    xs = [float(p["x_mm"]) for p in points]
    ys = [float(p["y_mm"]) for p in points]
    outline = (max(xs) - min(xs), max(ys) - min(ys))

    heights = [
        float(c.get("height_mm", 0.0))
        for c in board_report.get("component_heightmap") or []
        if c.get("side", "top") == "top"
    ]
    max_height = max(heights) if heights else 0.0

    mass = board_report.get("mass")
    mass_kg = float(mass["total_g"]) / 1000.0 if mass else None
    com = (
        (float(mass["com_mm"]["x_mm"]), float(mass["com_mm"]["y_mm"])) if mass else None
    )

    dissipation = sum(
        float(h.get("power_w") or 0.0) for h in board_report.get("thermal_hotspots") or []
    )

    connectors = {
        str(c["ref"]): (float(c["x_mm"]), float(c["y_mm"]))
        for c in board_report.get("connector_edges") or []
    }

    return BoardFacts(
        board_id=board_id,
        run_dir=run_dir,
        gate_status=status,
        mass_kg=mass_kg,
        com_mm=com,
        dissipation_w=dissipation,
        outline_mm=outline,
        max_component_height_mm=max_height,
        connectors=connectors,
        gate_detail=detail,
    )


def apply(ir: RobotIR, facts: BoardFacts, *, update_harnesses: bool = True) -> RobotIR:
    """A new RobotIR with one board's measured facts filled in.

    Does **not** raise on a failing gate. The failure is recorded on the board
    and `board_gate_passed` is a criterion that fails the robot for it — which is
    §12 #10 enforced where every other verdict is enforced, in the harness,
    rather than by an exception that a caller could wrap in a `try`.
    """
    if ir.electronics is None:
        raise ValueError(
            f"{ir.name!r} has no electronics subsystem, so there is no board "
            f"{facts.board_id!r} for these facts to land on"
        )

    data = ir.model_dump()
    boards = data["electronics"]["boards"]
    target = next((b for b in boards if b["id"] == facts.board_id), None)
    if target is None:
        raise KeyError(
            f"no board {facts.board_id!r} in {ir.name!r}; known boards: "
            f"{sorted(b['id'] for b in boards)}"
        )

    note = f"measured from the routed artifact in {facts.run_dir}"
    if facts.mass_kg is not None:
        target["measured_mass"] = Quantity(
            value=facts.mass_kg, unit="kg", provenance=_measured(facts.run_dir, note)
        ).model_dump()
    if facts.com_mm is not None:
        target["measured_com"] = Vec3(
            x=facts.com_mm[0], y=facts.com_mm[1], z=0.0
        ).model_dump()
    target["measured_dissipation"] = Quantity(
        value=facts.dissipation_w,
        unit="W",
        provenance=_measured(facts.run_dir, "sum of the run's thermal hotspots"),
    ).model_dump()
    target["measured_outline"] = Vec3(
        x=facts.outline_mm[0], y=facts.outline_mm[1], z=0.0
    ).model_dump()
    target["measured_max_component_height"] = Quantity(
        value=facts.max_component_height_mm,
        unit="mm",
        provenance=_measured(facts.run_dir, "tallest top-side part in the heightmap"),
    ).model_dump()
    target["gate_status"] = facts.gate_status
    target["run_dir"] = facts.run_dir

    updated = RobotIR.model_validate(data)
    if update_harnesses and facts.connectors:
        updated = derive_harness_lengths(updated, facts)
    return updated


def derive_harness_lengths(ir: RobotIR, facts: BoardFacts) -> RobotIR:
    """Replace guessed harness lengths with ones derived from real geometry.

    §7.3: "Connector positions from Circuit JSON — harness lengths stop being
    guesses; harness resistance feeds the voltage-drop term in the actuator
    model (§3)."

    The route is straight-line plus a slack allowance, not a cable path. A robot
    with a routed cable channel could do better; nothing here knows where the
    channel is, and a straight line with stated slack is honestly INFERRED
    whereas a fabricated path would look like more than it is. Slack matters —
    a cable pulled taut across a joint fails on the first full-range motion —
    so it is 25%, stated, and it makes the drop estimate conservative.
    """
    if ir.electronics is None:
        return ir

    frames = link_frames(ir)
    board = ir.electronics.board(facts.board_id)
    board_origin = link_geometry_transform(ir, board.mounted_on, frames)[:3, 3]

    data = ir.model_dump()
    changed = False
    for harness in data["electronics"]["harnesses"]:
        if harness["from_board"] != facts.board_id:
            continue
        joint = next((j for j in ir.joints if j.id == harness["to"]), None)
        if joint is None:
            continue  # board-to-board; both endpoints move, leave the stated length

        # The connector serving this run, if the harness names one. Falling back
        # to the board origin rather than skipping: a length from the board's
        # mounting point is still far better than the guess it replaces, and the
        # provenance note says which of the two it is.
        ref = next((c for c in facts.connectors if c in harness["id"]), None)
        if ref is not None:
            cx, cy = facts.connectors[ref]
            connector_world = board_origin + np.array([cx / 1000.0, cy / 1000.0, 0.0])
            basis = f"connector {ref} at ({cx:.1f}, {cy:.1f})mm on board {board.id!r}"
        else:
            connector_world = board_origin
            basis = f"board {board.id!r} origin — no connector in the report matches this harness id"

        joint_world = _joint_position(ir, joint, frames)
        straight = float(np.linalg.norm(joint_world - connector_world))
        length = straight * 1.25

        harness["length"] = Quantity(
            value=length,
            unit="m",
            provenance=Provenance(
                status="INFERRED",
                source=facts.run_dir,
                note=(
                    f"{straight * 1000:.0f}mm straight line + 25% slack, from {basis} "
                    f"to joint {joint.id!r}"
                ),
            ),
        ).model_dump()
        changed = True

    return RobotIR.model_validate(data) if changed else ir


def _joint_position(ir: RobotIR, joint, frames) -> np.ndarray:
    from engine.kinematics import joint_world_frame

    return joint_world_frame(ir, joint, frames)[:3, 3]


def from_run_dir(
    ir: RobotIR,
    board_id: str,
    run_dir: str | Path,
    *,
    board_report_path: str | Path | None = None,
) -> RobotIR:
    """The I/O edge: read a `pcb-ai` run directory and apply what it measured.

    Kept to this one function on purpose. Everything above is a pure transform
    over parsed data, so the whole ingest path is testable without a run
    directory and `evaluate()` never touches the filesystem.
    """
    run = Path(run_dir)
    summary_path = run / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(
            f"{summary_path} does not exist — a `pcb-ai` run directory always has one, "
            "so either the run did not finish or this is not a run directory"
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    if board_report_path is None:
        # Where `dump-board-report.ts` writes it, keyed by run name.
        candidates = [
            run.parent.parent / "cad-generation" / "designs" / "board_reports" / f"{run.name}.board_report.json",
            run / "board_report.json",
            run / "handoff" / "board_report.json",
        ]
        board_report_path = next((p for p in candidates if p.exists()), None)
        if board_report_path is None:
            raise FileNotFoundError(
                "no board report found for this run. Generate one with "
                f"`npx tsx src/cad/dump-board-report.ts {run.name}` in pcb-ai, or pass "
                "board_report_path explicitly."
            )
    report = json.loads(Path(board_report_path).read_text(encoding="utf-8"))

    return apply(ir, board_facts(board_id, report, summary, str(run)))


def board_mass_properties(ir: RobotIR) -> dict[str, float]:
    """Measured board mass per link, kg — for folding into the robot's mass model.

    Returned as a plain mapping rather than merged into `MassProperties` because
    the mass-properties layer computes from geometry and this does not: a board
    is a purchased, measured object that sits in a bay, and pretending the CAD
    integrated it would put a number in the one place §12 #2 says numbers are
    computed rather than read.
    """
    if ir.electronics is None:
        return {}
    per_link: dict[str, float] = {}
    for board in ir.electronics.boards:
        if board.measured_mass is None:
            continue
        per_link[board.mounted_on] = per_link.get(board.mounted_on, 0.0) + board.measured_mass.magnitude_in("kg")
    return per_link


def unmeasured_boards(ir: RobotIR) -> list[str]:
    """Boards still carrying no measured facts — Phase 1's "50 g placeholder" list."""
    if ir.electronics is None:
        return []
    return [b.id for b in ir.electronics.boards if not b.designed]
