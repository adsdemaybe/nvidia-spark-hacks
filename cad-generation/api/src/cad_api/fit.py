"""Deterministic fit checking and board constraint derivation.

§6 is explicit: "Both `check_fit`s are deterministic geometry checks — agents
never adjudicate fit." Nothing in this module calls a model, reads a prompt, or
consults anything but numbers. It is the CAD half of the negotiation's referee.

Every violation reports `measured` and `limit` in a stated unit rather than a
bare boolean, so the negotiator can tell a 0.2 mm interference from a 40 mm one
and pick the side with more freedom to move (§6, and §8's ratio-over-boolean
rule).

The thresholds below are **engineering policy**, not physical constants — they
carry no provenance because they are requirements we chose, not facts about the
world. Same distinction `engine.criteria.builtin` draws for its own thresholds.
"""

from __future__ import annotations

import math

from cad_api.contracts import (
    BoardReport,
    EnclosureIntent,
    EnclosureReport,
    Envelope,
    FitResult,
    Keepout,
    MountingHole,
    Violation,
)

# --- policy thresholds --------------------------------------------------

_HOLE_MATCH_TOL_MM = 0.5  # standoff centre must land within this of the board hole
_HOLE_DIAMETER_SLOP_MM = 0.2  # standoff pilot may differ from board hole by this
_CUTOUT_MARGIN_MM = 0.0  # a cutout must at least fully cover its connector opening
_SEALED_POWER_BUDGET_W = 5.0  # above this, a sealed enclosure gets a thermal flag
# `measured` when there is no standoff at all to measure a distance to. Finite
# because `float("inf")` serialises as bare `Infinity`, which is not valid JSON
# and fails `JSON.parse` on the TypeScript half of this contract.
_NO_STANDOFF_MM = 1.0e6


def check_fit(board: BoardReport, enclosure: EnclosureReport) -> FitResult:
    """The §6 `cad.check_fit(board_report) -> violations[]`.

    Pure geometry. Returns every violation it finds rather than short-circuiting,
    because the negotiator needs the full picture to decide which side moves.
    """
    v: list[Violation] = []
    v += _check_footprint(board, enclosure)
    v += _check_height(board, enclosure)
    v += _check_bottom_clearance(board, enclosure)
    v += _check_secured(board, enclosure)
    v += _check_mounting(board, enclosure)
    v += _check_connectors(board, enclosure)
    v += _check_keepouts(board, enclosure)
    v += _check_thermal(board, enclosure)

    blocking = [x for x in v if x.severity in ("blocker", "major")]
    return FitResult(ok=not blocking, violations=v)


def _check_footprint(board: BoardReport, enc: EnclosureReport) -> list[Violation]:
    """The board must be small enough for the cavity *and* sit inside it.

    Size and position are separate failures. An enclosure we generated always
    places the board at `board_origin_mm` by construction, but `/cad/check_fit`
    accepts an externally supplied enclosure_report, where a board that fits by
    extent can still be positioned to hang over the cavity wall. Checking extent
    alone would pass it.
    """
    bb = board.outline_mm.bbox()
    origin = enc.board_origin_mm
    out: list[Violation] = []
    for axis, board_min, board_max, board_extent, cavity_extent, off in (
        ("x", bb.min_x_mm, bb.max_x_mm, bb.length_mm, enc.cavity_mm.length_mm, origin.x_mm),
        ("y", bb.min_y_mm, bb.max_y_mm, bb.width_mm, enc.cavity_mm.width_mm, origin.y_mm),
    ):
        if board_extent > cavity_extent:
            out.append(
                Violation(
                    code="board_exceeds_cavity",
                    severity="blocker",
                    detail=f"board {axis}-extent {board_extent:.2f}mm exceeds cavity {cavity_extent:.2f}mm",
                    measured=board_extent,
                    limit=cavity_extent,
                    unit="mm",
                    ref=axis,
                )
            )
            continue  # it also overhangs, but the size is the fact worth reporting

        lo, hi = board_min + off, board_max + off
        overhang = max(-lo, hi - cavity_extent)
        if overhang > 0:
            out.append(
                Violation(
                    code="board_overhangs_cavity",
                    severity="blocker",
                    detail=(
                        f"board fits the cavity {axis}-extent but sits at "
                        f"[{lo:.2f},{hi:.2f}]mm, outside the cavity [0.00,{cavity_extent:.2f}]mm"
                    ),
                    measured=overhang,
                    limit=0.0,
                    unit="mm",
                    ref=axis,
                )
            )
    return out


def _check_height(board: BoardReport, enc: EnclosureReport) -> list[Violation]:
    standoff_h = max((s.height_mm for s in enc.standoff_positions), default=0.0)
    stack = standoff_h + board.thickness_mm + board.max_component_height_mm("top")
    if stack > enc.cavity_mm.height_mm:
        return [
            Violation(
                code="stack_exceeds_cavity_height",
                severity="blocker",
                detail=(
                    f"standoff {standoff_h:.2f} + board {board.thickness_mm:.2f} + tallest top "
                    f"component {board.max_component_height_mm('top'):.2f} = {stack:.2f}mm "
                    f"exceeds cavity height {enc.cavity_mm.height_mm:.2f}mm"
                ),
                measured=stack,
                limit=enc.cavity_mm.height_mm,
                unit="mm",
            )
        ]
    return []


def _check_bottom_clearance(board: BoardReport, enc: EnclosureReport) -> list[Violation]:
    """Bottom-side components hang into the standoff gap and must clear the floor.

    `_check_height` measures the stack *above* the board; this is the other half.
    Without it a board with a 12mm electrolytic underneath, seated on 4mm
    standoffs, passes every check while being physically unseatable — the part
    reaches the cavity floor 8mm before the standoffs do.

    `cad_api.geometry.standoff_height_mm` sizes generated enclosures so this never
    fires on our own output; it fires on an externally supplied enclosure_report,
    which `/cad/check_fit` accepts and must not take on trust.
    """
    below = board.max_component_height_mm("bottom")
    if below <= 0:
        return []
    standoff_h = max((s.height_mm for s in enc.standoff_positions), default=0.0)
    if below <= standoff_h:
        return []
    return [
        Violation(
            code="bottom_component_exceeds_standoff",
            severity="blocker",
            detail=(
                f"tallest bottom-side component {below:.2f}mm exceeds the standoff "
                f"height {standoff_h:.2f}mm — the board cannot seat"
            ),
            measured=below,
            limit=standoff_h,
            unit="mm",
        )
    ]


def _check_secured(board: BoardReport, enc: EnclosureReport) -> list[Violation]:
    """A board with no mounting holes gets no standoffs, so nothing holds it.

    The enclosure is still geometrically valid, which is precisely why this needs
    saying out loud — every other check passes and the board rattles. Minor, not
    blocking: snap-fit and rail-retained designs are legitimate, and this API
    cannot tell those apart from an oversight. It reports the fact and lets a
    human decide.
    """
    if board.mounting_holes or enc.standoff_positions:
        return []
    return [
        Violation(
            code="board_not_mechanically_secured",
            severity="minor",
            detail=(
                "board declares no mounting holes, so the enclosure has no standoffs — "
                "nothing constrains the board inside the cavity. Intentional for snap-fit "
                "or rail designs; an oversight otherwise"
            ),
            measured=0.0,
            limit=1.0,
            unit="count",
        )
    ]


def _check_mounting(board: BoardReport, enc: EnclosureReport) -> list[Violation]:
    """Every board mounting hole needs a standoff under it, with a usable pilot."""
    out: list[Violation] = []
    origin = enc.board_origin_mm
    for hole in board.mounting_holes:
        want_x = hole.x_mm + origin.x_mm
        want_y = hole.y_mm + origin.y_mm
        best = None
        best_d = math.inf
        for s in enc.standoff_positions:
            d = math.hypot(s.x_mm - want_x, s.y_mm - want_y)
            if d < best_d:
                best, best_d = s, d

        if best is None or best_d > _HOLE_MATCH_TOL_MM:
            out.append(
                Violation(
                    code="mounting_hole_unsupported",
                    severity="major",
                    detail=(
                        f"no standoff within {_HOLE_MATCH_TOL_MM}mm of board hole at "
                        f"({hole.x_mm:.2f},{hole.y_mm:.2f})mm"
                        + ("" if best is None else f"; nearest is {best_d:.2f}mm away")
                    ),
                    measured=best_d if best is not None else _NO_STANDOFF_MM,
                    limit=_HOLE_MATCH_TOL_MM,
                    unit="mm",
                    ref=f"hole@{hole.x_mm:.1f},{hole.y_mm:.1f}",
                )
            )
            continue

        slop = abs(best.hole_diameter_mm - hole.diameter_mm)
        if slop > _HOLE_DIAMETER_SLOP_MM:
            out.append(
                Violation(
                    code="mounting_hole_diameter_mismatch",
                    severity="minor",
                    detail=(
                        f"standoff pilot {best.hole_diameter_mm:.2f}mm vs board hole "
                        f"{hole.diameter_mm:.2f}mm"
                    ),
                    measured=slop,
                    limit=_HOLE_DIAMETER_SLOP_MM,
                    unit="mm",
                    ref=f"hole@{hole.x_mm:.1f},{hole.y_mm:.1f}",
                )
            )
    return out


def _check_connectors(board: BoardReport, enc: EnclosureReport) -> list[Violation]:
    """A connector asking for a cutout must have one that fully covers it."""
    out: list[Violation] = []
    by_ref = {c.ref: c for c in enc.port_cutouts}
    for conn in board.connector_edges:
        if not conn.needs_cutout:
            continue
        cut = by_ref.get(conn.ref)
        if cut is None:
            out.append(
                Violation(
                    code="connector_without_cutout",
                    severity="blocker",
                    detail=f"connector {conn.ref!r} on {conn.edge} wall has no port cutout",
                    measured=0.0,
                    limit=conn.width_mm,
                    unit="mm",
                    ref=conn.ref,
                )
            )
            continue
        if cut.edge != conn.edge:
            out.append(
                Violation(
                    code="cutout_wrong_edge",
                    severity="blocker",
                    detail=f"connector {conn.ref!r} is on {conn.edge} but its cutout is on {cut.edge}",
                    measured=0.0,
                    limit=1.0,
                    unit="bool",
                    ref=conn.ref,
                )
            )
            continue
        for dim, want, got in (("width", conn.width_mm, cut.width_mm), ("height", conn.height_mm, cut.height_mm)):
            if got + _CUTOUT_MARGIN_MM < want:
                out.append(
                    Violation(
                        code="cutout_too_small",
                        severity="major",
                        detail=f"cutout {dim} {got:.2f}mm is smaller than connector {dim} {want:.2f}mm",
                        measured=got,
                        limit=want,
                        unit="mm",
                        ref=conn.ref,
                    )
                )
    return out


def _check_keepouts(board: BoardReport, enc: EnclosureReport) -> list[Violation]:
    """A standoff boss must not land inside a declared keepout.

    Only *bottom*-side keepouts can be intruded by a standoff. A standoff lives
    entirely under the board; a top-side keepout (an antenna, a mezzanine header)
    is on the far side of the PCB and cannot touch one. Testing every keepout
    regardless of side produced a phantom `standoff_in_keepout` blocker, which in
    the §6 negotiation is worse than a missed one — it drives a re-place that
    cannot converge, because there was nothing to fix.
    """
    out: list[Violation] = []
    origin = enc.board_origin_mm
    for ko in board.keepouts:
        if ko.side != "bottom":
            continue
        x0 = ko.x_mm + origin.x_mm
        y0 = ko.y_mm + origin.y_mm
        x1, y1 = x0 + ko.width_mm, y0 + ko.depth_mm
        for s in enc.standoff_positions:
            r = s.outer_diameter_mm / 2
            # closest point on the keepout rectangle to the boss centre
            cx = min(max(s.x_mm, x0), x1)
            cy = min(max(s.y_mm, y0), y1)
            d = math.hypot(s.x_mm - cx, s.y_mm - cy)
            if d < r:
                out.append(
                    Violation(
                        code="standoff_in_keepout",
                        severity="major",
                        detail=(
                            f"standoff at ({s.x_mm:.2f},{s.y_mm:.2f})mm r={r:.2f}mm intrudes "
                            f"{r - d:.2f}mm into keepout {ko.name!r}"
                        ),
                        measured=r - d,
                        limit=0.0,
                        unit="mm",
                        ref=ko.name,
                    )
                )
    return out


def _check_thermal(board: BoardReport, enc: EnclosureReport) -> list[Violation]:
    """A sealed box over the power budget gets flagged — minor, because this API
    does no thermal simulation and must not pretend otherwise. A real verdict
    needs the physics tier; this is a tripwire, not an analysis."""
    total = board.total_power_w()
    if total > _SEALED_POWER_BUDGET_W and not enc.port_cutouts:
        return [
            Violation(
                code="sealed_enclosure_thermal_risk",
                severity="minor",
                detail=(
                    f"{total:.2f}W dissipated in a sealed enclosure with no openings; "
                    f"no thermal simulation was run — this is a flag, not a verdict"
                ),
                measured=total,
                limit=_SEALED_POWER_BUDGET_W,
                unit="W",
            )
        ]
    return []


# --- constrain_board ----------------------------------------------------


def constrain_board(
    reason: str,
    enc: EnclosureReport,
    intent: EnclosureIntent,
    board_thickness_mm: float | None = None,
) -> Envelope:
    """The §6 `cad.constrain_board(reason) -> envelope`.

    Expresses, in *board* coordinates, the largest board this enclosure accepts.
    The PCB side feeds this straight into `pcb.replace_within(envelope)`.

    `board_thickness_mm` defaults to the contract's own default rather than a
    number retyped here, and callers that know the real board should pass it: the
    height budget handed back to the PCB side is wrong by the difference, which is
    0.8mm of headroom silently granted or withheld on a 2.4mm board.
    """
    if board_thickness_mm is None:
        board_thickness_mm = BoardReport.model_fields["thickness_mm"].default
    origin = enc.board_origin_mm
    clearance = intent.clearance_mm

    # Usable cavity, inset by the clearance, expressed back in board coordinates.
    max_outline = _bbox(
        min_x=clearance - origin.x_mm,
        min_y=clearance - origin.y_mm,
        max_x=enc.cavity_mm.length_mm - clearance - origin.x_mm,
        max_y=enc.cavity_mm.width_mm - clearance - origin.y_mm,
    )

    standoff_h = max((s.height_mm for s in enc.standoff_positions), default=intent.standoff_height_mm)
    max_top = max(enc.cavity_mm.height_mm - standoff_h - board_thickness_mm, 0.0)

    holes = [
        MountingHole(
            x_mm=s.x_mm - origin.x_mm,
            y_mm=s.y_mm - origin.y_mm,
            diameter_mm=s.hole_diameter_mm,
        )
        for s in enc.standoff_positions
    ]

    # Each standoff boss is a region the board must keep clear of components.
    keepouts = [
        Keepout(
            name=f"standoff@{s.x_mm:.1f},{s.y_mm:.1f}",
            x_mm=s.x_mm - origin.x_mm - s.outer_diameter_mm / 2,
            y_mm=s.y_mm - origin.y_mm - s.outer_diameter_mm / 2,
            width_mm=s.outer_diameter_mm,
            depth_mm=s.outer_diameter_mm,
            height_mm=s.height_mm,
            side="bottom",
        )
        for s in enc.standoff_positions
    ]

    return Envelope(
        reason=reason,
        max_outline_mm=max_outline,
        max_component_height_mm=max_top,
        max_bottom_component_height_mm=standoff_h,
        mounting_hole_pattern=holes,
        keepouts=keepouts,
    )


def _bbox(min_x: float, min_y: float, max_x: float, max_y: float):
    from cad_api.contracts import BBox2

    return BBox2(min_x_mm=min_x, min_y_mm=min_y, max_x_mm=max_x, max_y_mm=max_y)
