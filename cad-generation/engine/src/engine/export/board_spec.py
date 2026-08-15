"""Robot -> `pcb-ai`: the down direction of the §7 contract.

§7.1 is explicit that the two codebases are never linked. This module produces
the two files that cross the boundary — `board-spec.md`, which `pcb-ai`'s intake
and its specification reviewer read requirement by requirement, and
`envelope.json`, which is its `Envelope` zod schema exactly — and nothing else.
No subprocess, no import, no shared object. Running `pcb-ai` is the caller's
job; this is a pure `RobotIR -> (str, dict)` transform, so it stays inside §12
non-negotiable #7 (the engine has zero I/O) and can be tested without either
codebase being installed.

What the robot knows that the board cannot work out for itself, and that is
therefore the entire point of the file (§7.2):

- **The bay.** The board cannot be designed bigger than the space it bolts into.
  This comes from the CAD `pcb_bay` geometry, so the constraint is the real one
  rather than a number somebody remembered.
- **The budgets.** Worst-case per-motor current at stall, from the tier-0 energy
  analysis, with the margin policy stated rather than folded in — a fab reading
  "8 A" needs to know whether that is the draw or the draw plus headroom.
- **The intent behind connector placement.** "Motor connectors on the edge
  facing the harness channel" is ergonomics the board has no way to infer;
  written as `at_edge` rules it becomes something `pcb-ai` enforces
  deterministically at its L3, so the robot's intent becomes the board's hard
  gate instead of a comment nobody reads.

Millimetres throughout, because that is the PCB world's native unit and the
contract file says so. The IR works in SI metres and converts here, at the edge.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from engine.catalogue import MotorSpec, resolve as resolve_catalogue
from engine.electrical import motor_worst_case_current, rail_operating_point
from engine.ir import BoardSpec, RobotIR

# Headroom over the computed worst case, and it is stated in the spec rather
# than silently folded into the number. A fab that reads "8.0 A" and a fab that
# reads "6.4 A worst case, budget 8.0 A at 1.25x" size the same trace and only
# one of them can tell you why.
DEFAULT_MARGIN = 1.25

# The DFM profile `pcb-ai` derives from a `.kicad_pro`. Chosen once for the
# project (§7.2) rather than per board, because a robot whose three boards go to
# three different fabs has three different minimum trace widths and no single
# design rule — a real way to lose a week.
DEFAULT_FAB_PROFILE = "jlcpcb-2layer"


@dataclass(frozen=True)
class BoardSpecArtifacts:
    """The two files that cross the boundary, plus the hash that gates a respin."""

    board_id: str
    markdown: str
    envelope: dict
    spec_hash: str


def _mm(metres: float) -> float:
    return metres * 1000.0


def spec_hash(ir: RobotIR, board_id: str) -> str:
    """Hash of exactly what `pcb-ai` reads — rails, envelope, connectors, budgets.

    §4: "`pcb-ai` runs are never inside the optimizer's inner loop... re-runs
    `pcb-ai` only when the electronics *spec hash* changes". Which means this
    hash has to be narrow. Hashing the whole IR would respin the board every
    time a bracket got 2 mm longer, and a board respin costs minutes — the cache
    would never hit and the rule would be decorative.

    So: the bay envelope, the rails the board touches, its budgets, its mounting
    pattern, its connector rules. Not the chassis, not the arm, not the joint
    limits, not anything the board cannot see.
    """
    board = _board(ir, board_id)
    assert ir.electronics is not None

    payload = {
        "board": board.id,
        "purpose": board.purpose,
        "max_outline_mm": [board.max_outline.x, board.max_outline.y],
        "max_component_height_mm": board.max_component_height.magnitude_in("mm"),
        "mount": (
            {
                "hole_diameter_mm": board.mount.hole_diameter.magnitude_in("mm"),
                "positions_mm": sorted(
                    [_mm(p.x), _mm(p.y)] for p in board.mount.positions
                ),
            }
            if board.mount is not None
            else None
        ),
        "keepouts": sorted(board.keepouts),
        "connector_rules": sorted(board.connector_rules),
        "rails": sorted(
            {
                "id": r.id,
                "voltage_v": r.voltage.magnitude_in("V"),
                "budget_a": r.budget_current.magnitude_in("A"),
            }
            for r in ir.electronics.rails
            if r.id in board.rails
        ),
        "fab_profile": DEFAULT_FAB_PROFILE,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _board(ir: RobotIR, board_id: str) -> BoardSpec:
    if ir.electronics is None:
        raise ValueError(
            f"{ir.name!r} has no electronics subsystem, so it has no board {board_id!r} "
            "to specify. Add rails and boards to the IR first — a board spec invented "
            "here would be a number no criterion ever checked."
        )
    return ir.electronics.board(board_id)


def envelope(ir: RobotIR, board_id: str) -> dict:
    """`pcb-ai`'s `Envelope`, as a dict ready to be written as JSON.

    Field names and units are that schema's, not ours — the contract is the file,
    and a rename on either side has to break loudly. The board origin is the
    bay's corner, so the outline runs 0..max rather than being centred: MuJoCo
    and OpenCascade centre things, KiCad does not, and picking the PCB
    convention here means the conversion happens once, visibly, at the boundary.
    """
    board = _board(ir, board_id)
    # Keepouts are stated as *reasons* in the IR ("swing of the shoulder link"),
    # because that is what the mechanical side knows. The contract's `Keepout` is
    # a rectangle with a positive width and depth, and we do not have one: the
    # swept volume is tier-2 work that does not exist yet.
    #
    # So they go in `reason`, in words, rather than into `keepouts` as zero-area
    # rectangles. A zero-area keepout is not a weaker keepout — it is a keepout
    # that constrains nothing, which reads to every downstream check as "there
    # are no keepouts on this board". Words the intake agent must act on beat a
    # structured field that silently means nothing.
    reason = (
        f"{board.purpose} for {ir.name!r}, mounted on link {board.mounted_on!r}. "
        "Outline and height are the bay's interior; a board larger than this does "
        "not physically go in."
    )
    if board.keepouts:
        reason += (
            " MECHANICAL KEEPOUTS, not yet expressible as rectangles and therefore "
            "not in the keepouts field — treat each as a hard constraint and place "
            "it by hand: " + "; ".join(board.keepouts) + "."
        )

    out: dict = {
        "reason": reason,
        "max_outline_mm": {
            "min_x_mm": 0.0,
            "min_y_mm": 0.0,
            "max_x_mm": board.max_outline.x,
            "max_y_mm": board.max_outline.y,
        },
        "max_component_height_mm": board.max_component_height.magnitude_in("mm"),
        # Zero unless the bay says otherwise: a board that bolts flat to standoffs
        # has its bottom side against the mount, and a tall part there is an
        # interference nobody sees until assembly.
        "max_bottom_component_height_mm": 0.0,
        "mounting_hole_pattern": [],
        "keepouts": [],
    }
    if board.mount is not None:
        diameter = board.mount.hole_diameter.magnitude_in("mm")
        out["mounting_hole_pattern"] = [
            {
                "x_mm": _mm(p.x),
                "y_mm": _mm(p.y),
                "diameter_mm": diameter,
                "plated": False,
            }
            for p in board.mount.positions
        ]
    return out


def _rail_rows(ir: RobotIR, board: BoardSpec, margin: float) -> list[str]:
    assert ir.electronics is not None
    rows: list[str] = []
    for rail_id in board.rails:
        rail = ir.electronics.rail(rail_id)
        op = rail_operating_point(ir, rail_id)
        budget = rail.budget_current.magnitude_in("A")
        rows.append(
            f"| `{rail_id}` | {rail.voltage.magnitude_in('V'):.2f} V | "
            f"{op.current_a:.2f} A | {op.current_a * margin:.2f} A | "
            f"{budget:.2f} A | {op.provenance} |"
        )
    return rows


def _load_rows(ir: RobotIR, board: BoardSpec) -> list[str]:
    assert ir.electronics is not None
    rows: list[str] = []
    for joint in ir.joints:
        if joint.actuator is None:
            continue
        rail_id = ir.electronics.joint_rail.get(joint.id)
        if rail_id is None or rail_id not in board.rails:
            continue
        motor: MotorSpec = resolve_catalogue(joint.actuator.catalogue, joint.actuator.value)
        amps, status, note = motor_worst_case_current(motor)
        rows.append(
            f"| `{joint.id}` | {motor.part_number or motor.key} | `{rail_id}` | "
            + (f"{amps:.2f} A" if amps is not None else "**unknown**")
            + f" | {status} | {note} |"
        )
    return rows


def markdown(ir: RobotIR, board_id: str, *, margin: float = DEFAULT_MARGIN) -> str:
    """The specification `pcb-ai`'s intake and its spec reviewer read.

    Written as numbered requirements rather than prose on purpose: the reviewing
    agent is told to "go through the specification requirement by requirement",
    so a requirement it can quote is a requirement it can check, and a paragraph
    is a requirement it can agree with in general terms while the board misses it.
    """
    board = _board(ir, board_id)
    assert ir.electronics is not None
    env = envelope(ir, board_id)

    rail_rows = _rail_rows(ir, board, margin)
    load_rows = _load_rows(ir, board)

    lines: list[str] = [
        f"# {board.purpose} — `{board.id}`",
        "",
        f"Board for the robot `{ir.name}`, mounted on link `{board.mounted_on}`.",
        "",
        "Generated from the robot IR by `engine.export.board_spec`. Every number below "
        "is computed from the mechanical design or read from the shared parts catalogue "
        "— none of it was typed in for this board, and none of it may be renegotiated "
        "on the board side. Where the robot cannot supply a number, this says so "
        "explicitly rather than omitting the requirement.",
        "",
        f"Spec hash: `{spec_hash(ir, board_id)[:16]}` — the board is re-designed when "
        "and only when this changes.",
        "",
        "## 1. Mechanical envelope (hard)",
        "",
        f"1.1 The board outline must fit within **{board.max_outline.x:.1f} x "
        f"{board.max_outline.y:.1f} mm**. This is the interior of the bay in "
        f"`{board.mounted_on}`; a larger board does not physically go in.",
        "",
        f"1.2 No top-side component may exceed **"
        f"{board.max_component_height.magnitude_in('mm'):.1f} mm** above the board.",
        "",
        "1.3 Bottom-side components are not permitted: the board seats on standoffs "
        "against the bay floor.",
        "",
    ]

    holes = env["mounting_hole_pattern"]
    if holes:
        lines += [
            f"1.4 The board must carry **{len(holes)} mounting holes** of "
            f"{holes[0]['diameter_mm']:.1f} mm diameter at these positions, in board "
            "coordinates with the origin at the bay's corner:",
            "",
            "| # | x (mm) | y (mm) |",
            "|---|---|---|",
        ]
        lines += [
            f"| {i + 1} | {h['x_mm']:.2f} | {h['y_mm']:.2f} |" for i, h in enumerate(holes)
        ]
        lines.append("")
    else:
        # The defect the full-stack run found the first time it ran: no rover
        # board had mounting holes, so every enclosure generated zero standoffs.
        # Saying it out loud in the spec is cheaper than finding it again.
        lines += [
            "1.4 **No mounting pattern is specified, and this is a defect in the robot "
            "design, not licence to omit holes.** A board with no holes gives the "
            "enclosure nothing to stand it off, and the assembly has no way to hold it. "
            "Add a `MountPattern` to the IR before this board is fabricated.",
            "",
        ]

    if board.keepouts:
        lines += [
            "1.5 Keepouts — areas of the bay a mechanism passes through. These are "
            "stated as reasons, not rectangles, because the swept volume is not yet "
            "computed; treat each as a hard constraint to be placed by hand:",
            "",
        ]
        lines += [f"- {reason}" for reason in board.keepouts]
        lines.append("")

    lines += [
        "## 2. Rails and current budgets (hard)",
        "",
        f"Budgets are the computed worst case multiplied by a stated margin of "
        f"**{margin:.2f}x**. The margin is shown separately so trace sizing can be "
        "checked against either figure.",
        "",
        "| rail | voltage | worst case | with margin | IR budget | provenance |",
        "|---|---|---|---|---|---|",
    ]
    lines += rail_rows or ["| _none_ | | | | | |"]
    lines += [
        "",
        "2.1 Every rail above must be sized for the **with margin** column, "
        "continuously, at the stated voltage.",
        "",
    ]

    if load_rows:
        lines += [
            "2.2 The loads making up those budgets, so a reviewer can check the sum:",
            "",
            "| joint | actuator | rail | worst-case draw | provenance | basis |",
            "|---|---|---|---|---|---|",
        ]
        lines += load_rows
        lines.append("")

    unknown = [r for r in load_rows if "**unknown**" in r]
    if unknown:
        lines += [
            "2.3 **Some loads above have no current figure in the catalogue.** Their "
            "draw is missing from the budget, so the budget is a lower bound. Do not "
            "size to it without sourcing the missing figures first (§6.1).",
            "",
        ]

    lines += [
        "## 3. Connector placement (hard, enforced at L3)",
        "",
    ]
    if board.connector_rules:
        lines += [
            "These are the robot's harness routing expressed as placement rules. They "
            "are not preferences — a connector on the wrong edge means a cable that "
            "crosses a moving joint.",
            "",
        ]
        lines += [f"- `{rule}`" for rule in board.connector_rules]
    else:
        lines.append(
            "No placement rules are specified. Connector positions are therefore free, "
            "and the harness lengths in the robot IR are guesses until they come back "
            "from Circuit JSON."
        )
    lines += [
        "",
        "## 4. Fabrication",
        "",
        f"4.1 Design against the **`{DEFAULT_FAB_PROFILE}`** DFM profile. It is chosen "
        "once for the whole robot: three boards against three fabs have three different "
        "minimum trace widths and no single design rule.",
        "",
        "## 5. What this specification does not constrain",
        "",
        "Topology, part selection within the shared catalogue, layer stackup beyond the "
        "fab profile, and routing are the board side's to decide. The robot side "
        "constrains only what it can measure: space, current, and where cables leave.",
        "",
        "## 6. Return contract",
        "",
        "The run directory is read back by `engine.ingest.pcb_run`, which lifts board "
        "mass, centre of mass, dissipation, outline and gate status into the robot IR "
        "as MEASURED-class facts. A hard gate failure on this board is a hard failure "
        "of the robot: no robot-side agent can waive it (§12 non-negotiable #10).",
        "",
    ]
    return "\n".join(lines)


def emit(ir: RobotIR, board_id: str, *, margin: float = DEFAULT_MARGIN) -> BoardSpecArtifacts:
    """Both artifacts and the hash, as data. Writing them is the caller's job."""
    return BoardSpecArtifacts(
        board_id=board_id,
        markdown=markdown(ir, board_id, margin=margin),
        envelope=envelope(ir, board_id),
        spec_hash=spec_hash(ir, board_id),
    )


def emit_all(ir: RobotIR, *, margin: float = DEFAULT_MARGIN) -> list[BoardSpecArtifacts]:
    if ir.electronics is None:
        return []
    return [emit(ir, b.id, margin=margin) for b in ir.electronics.boards]


def main(argv: list[str] | None = None) -> int:
    """`python -m engine.export.board_spec ir.json --out runs/spec` — §7.5 step I1.

    The I/O edge, and the only one in this module. Step I1 of the integration
    sequence is deliberately manual — "robot emits `board-spec.md`; a human runs
    `pcb-ai`; robot ingests the run dir. Proves the contract with zero
    orchestration code" — so this writes files and stops.
    """
    import argparse
    import json as _json
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="python -m engine.export.board_spec")
    parser.add_argument("ir_path", help="path to a RobotIR JSON file")
    parser.add_argument("--board", default="", help="board id (default: every board)")
    parser.add_argument("--out", default=".", help="directory to write into")
    parser.add_argument(
        "--margin", type=float, default=DEFAULT_MARGIN, help="current budget margin"
    )
    args = parser.parse_args(argv)

    ir = RobotIR.model_validate(_json.loads(Path(args.ir_path).read_text(encoding="utf-8")))
    if ir.electronics is None:
        print(f"{ir.name}: no electronics subsystem — nothing to specify", flush=True)
        return 1

    boards = [args.board] if args.board else [b.id for b in ir.electronics.boards]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for board_id in boards:
        artifacts = emit(ir, board_id, margin=args.margin)
        spec_path = out_dir / f"{board_id}.board-spec.md"
        env_path = out_dir / f"{board_id}.envelope.json"
        spec_path.write_text(artifacts.markdown, encoding="utf-8")
        env_path.write_text(
            _json.dumps(artifacts.envelope, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"{board_id}: {spec_path} + {env_path}  spec_hash={artifacts.spec_hash[:16]}")
        print(f"  next: cd pcb-ai && npm run design -- --spec {spec_path.resolve()}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
