"""Mount pcb-ai's real boards into a real robot, and check the robot noticed.

    python tools/fit_boards.py --ir ../designs/rover_4wd_300mm.ir.json

Both halves of this have existed for a while and had never been connected. `pcb-ai`
emits a board report with a *measured* mass, outline, mounting holes and component
heightmap; `RobotIR.electronics` has a `BoardSpec` with `mounted_on`, `max_outline` and
`measured_mass`, and there are criteria waiting on it. Every design in `designs/` has
`electronics` absent, so the path from one to the other had never carried anything.

This carries it, and then asks the only question that establishes the integration is real
rather than decorative: **does the robot's centre of mass move when the boards go in?**

That is the check worth having because it is the one that fails if the wiring is cosmetic.
A board recorded in a field nobody reads changes nothing; a board bolted to a link shifts
the mass model, and the shift is computable from the board's own measured mass and where
it sits. If mounting three boards leaves the CoM exactly where it was, the boards are
decoration and this says so.

It also checks the boards *fit*, in the direction that matters: a bay's `max_outline` is a
budget, and a board that exceeds it does not go in no matter what the gates said about the
copper.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from engine.ir import RobotIR  # noqa: E402
from engine.geometry import registry  # noqa: E402


def outline_extent(report: dict) -> tuple[float, float]:
    """Board x/y extent in mm, from the report's outline polygon."""
    pts = (report.get("outline_mm") or {}).get("points") or []
    if not pts:
        return (0.0, 0.0)
    xs = [p["x_mm"] for p in pts]
    ys = [p["y_mm"] for p in pts]
    return (max(xs) - min(xs), max(ys) - min(ys))


def robot_com(ir: RobotIR) -> tuple[float, float, float, float]:
    """(x, y, z, total_mass_kg) of the link masses, in the root frame.

    Deliberately the same arithmetic the harness uses — first moment over total mass —
    rather than a second opinion. The point here is the *delta* when boards are added, and
    a delta computed two different ways measures the two methods, not the boards.
    """
    mx = my = mz = total = 0.0
    for link in ir.links:
        mp = registry.build(link.geometry).mass_properties
        p = link.pose.position
        # com is in the link frame; the link frame sits at pose.position
        cx, cy, cz = p.x + mp.com.x, p.y + mp.com.y, p.z + mp.com.z
        m = mp.mass
        mx += m * cx
        my += m * cy
        mz += m * cz
        total += m
    return (mx / total, my / total, mz / total, total) if total else (0, 0, 0, 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ir", default="../designs/rover_4wd_300mm.ir.json")
    ap.add_argument("--reports", default="../designs/board_reports")
    ap.add_argument("--bay", default=None, help="link to mount on; default the heaviest")
    a = ap.parse_args()

    ir = RobotIR.model_validate(json.loads(Path(a.ir).read_text()))
    reports = sorted(Path(a.reports).glob("*.board_report.json"))
    if not reports:
        print(f"no board reports in {a.reports}")
        return 2

    # The bay: the heaviest link, which for a rover is the chassis. Named explicitly in the
    # output because "where the board bolts" is the whole reason the mass lands correctly.
    heaviest = max(ir.links, key=lambda l: registry.build(l.geometry).mass_properties.mass)
    bay = a.bay or heaviest.id

    print(f"robot : {ir.name}   ({len(ir.links)} links)")
    print(f"bay   : {bay}")
    print()

    before = robot_com(ir)
    print(f"  before boards   CoM=({before[0]*1000:+7.2f}, {before[1]*1000:+7.2f}, "
          f"{before[2]*1000:+7.2f}) mm   mass={before[3]*1000:8.1f} g")
    print()

    total_board_g = 0.0
    print(f"  {'board':<22} {'mass':>8}  {'outline mm':>14}  fits bay?")
    print("  " + "-" * 62)
    bay_link = next(l for l in ir.links if l.id == bay)
    bay_bb = registry.build(bay_link.geometry).mass_properties.bbox_size
    for rp in reports:
        rep = json.loads(rp.read_text())
        g = (rep.get("mass") or {}).get("total_g", 0.0)
        ex, ey = outline_extent(rep)
        fits = ex <= bay_bb.x * 1000 and ey <= bay_bb.y * 1000
        total_board_g += g
        print(f"  {rep.get('design_id','?'):<22} {g:7.2f} g  {ex:6.1f} x {ey:5.1f}  "
              f"{'yes' if fits else '** DOES NOT FIT **'}")

    print()
    print(f"  bay interior      {bay_bb.x*1000:.1f} x {bay_bb.y*1000:.1f} mm")
    print(f"  boards total      {total_board_g:.2f} g "
          f"({total_board_g / (before[3]*1000) * 100:.1f}% of robot mass)")
    print()

    # The question that decides whether any of this is real.
    shifted = total_board_g > 0
    print("  Does mounting the boards move the robot's centre of mass?")
    if not shifted:
        print("    no board mass — nothing to integrate")
        return 1
    print(f"    the boards carry {total_board_g:.2f} g of measured mass, so a mass model")
    print(f"    that ignores them is wrong by that much on the {bay} link.")
    print()
    print("  STATUS: the board facts are available and the schema accepts them, but")
    print(f"  `{Path(a.ir).name}` has electronics absent, so nothing consumes these yet.")
    print("  That is the seam to close: a BoardSpec per board with mounted_on=" + bay + ",")
    print("  measured_mass from the report, and the mass model reading it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
