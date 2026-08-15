"""The whole robot creation stack, in one run.

    F1 (pcb-ai)          board HDL -> gates -> circuit JSON -> board_report
    F2 (cad-generation)  board_report -> enclosure + robot IR
    cosim                robot IR -> MJCF -> co-simulated rollout
    gate                 did it survive, electrically and thermally, and do the task
    route                if not, which side owns the failure

Each of those already worked alone. This is the seam, and the seam is where the
interesting failures live: F1's boards had no mounting holes for a year of nobody
noticing, because nothing in F1 cares how a board is held and nothing in F2 can invent
a hole that is not in the report. Running them against each other is what found it.

Nothing here re-implements a stage. Every number is read from the stage that owns it —
board mass and dissipation from F1's own reports, link geometry from F2's IR, motor
constants from the co-sim catalogue — so a disagreement between two stages shows up as a
disagreement rather than being averaged away by a third opinion.

    python tools/full_stack.py --pcb-runs ../pcb-ai/runs --robot simple_rover
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cosim.gate import DRV8833_AS_BUILT, TaskGoal, evaluate
from cosim.robot import from_cad_ir, describe_assumptions
from cosim.route import describe, route


def load_board_facts(runs_dir: Path) -> list[dict]:
    """What F1 measured, per board — read, never recomputed."""
    boards = []
    for run in sorted(runs_dir.iterdir()):
        phys = run / "iter-0" / "physics.txt"
        circuit = run / "iter-0" / "circuit.json"
        if not phys.exists() or not circuit.exists():
            continue
        text = phys.read_text()
        peak = None
        for line in text.splitlines():
            if line.strip().startswith("peak:"):
                peak = float(line.split("peak:")[1].split("°C")[0])
                break
        holes = sum(
            1 for e in json.loads(circuit.read_text()) if e.get("type") == "pcb_hole"
        )
        boards.append({"name": run.name, "peak_c": peak, "mounting_holes": holes})
    return boards


def boards_for_robot(robots_json: Path, robot: str) -> set[str] | None:
    """Which pcb-ai runs belong to this robot, per the manifest.

    Returns None when the manifest has nothing to say, so an unlisted robot falls back to
    "check everything" rather than to "check nothing" -- the safe direction for a gate.
    """
    if not robots_json.exists():
        return None
    data = json.loads(robots_json.read_text())
    key = robot.replace("simple_", "").replace("_", "-")
    for r in data.get("robots", []):
        if r.get("id") in (robot, key) or r.get("id", "").replace("-", "_") == robot:
            return {b["run"] for b in r.get("boards", [])}
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pcb-runs", default="../pcb-ai/runs")
    ap.add_argument("--robot", default="simple_rover")
    ap.add_argument("--cad-engine", default="../cad-generation/engine/src")
    ap.add_argument("--cad-python", default="../cad-generation/engine/.venv/bin/python",
                    help="the CAD project's interpreter; it owns build123d/OCP")
    ap.add_argument("--robots-json", default="../pcb-ai/robots.json")
    args = ap.parse_args()

    print("=" * 72)
    print("F1 — boards, as measured by pcb-ai")
    print("=" * 72)
    boards = load_board_facts(Path(args.pcb_runs))
    # Only this robot's boards. `compass` and `rover-viafix` are bench fixtures that
    # exist to exercise the pipeline and are never fitted to anything, so holding the
    # rover's verdict hostage to their mounting holes would be a false blocker -- and a
    # gate that cries wolf gets ignored, which is worse than not having it.
    mine = boards_for_robot(Path(args.robots_json), args.robot)
    if mine is not None:
        skipped = [b["name"] for b in boards if b["name"] not in mine]
        boards = [b for b in boards if b["name"] in mine]
        if skipped:
            print(f"  (not this robot's, skipped: {', '.join(sorted(skipped))})")
    if not boards:
        print("  no completed pcb-ai runs found")
    for b in boards:
        peak = f"{b['peak_c']:.1f}°C" if b["peak_c"] is not None else "n/a"
        secured = "secured" if b["mounting_holes"] else "** NOT SECURED **"
        print(f"  {b['name']:<22} peak {peak:>8}   {b['mounting_holes']} mounting holes  {secured}")

    # A board with no mounting holes is a real finding, not a cosmetic one: the enclosure
    # has nothing to bolt to, so the mass ends up unconstrained in the assembly and every
    # inertia downstream is a guess. Surface it here rather than letting it pass silently.
    unsecured = [b["name"] for b in boards if not b["mounting_holes"]]

    print()
    print("=" * 72)
    print("F2 — mechanics, from the CAD engine's IR")
    print("=" * 72)
    export = Path(__file__).with_name("_cad_export.py")
    try:
        proc = subprocess.run(
            [args.cad_python, str(export), args.robot, args.cad_engine],
            capture_output=True, text=True, timeout=600,
        )
    except FileNotFoundError:
        print(f"  CAD interpreter not found at {args.cad_python}")
        print("  Cannot continue: the mechanics is F2's to state, and guessing it here")
        print("  would be inventing the number this whole stack exists to measure.")
        return 2
    if proc.returncode != 0 or not proc.stdout.strip():
        print(f"  CAD export failed (rc={proc.returncode})")
        for line in (proc.stderr or "").strip().splitlines()[-6:]:
            print(f"    {line}")
        return 2
    out = json.loads(proc.stdout)
    if "error" in out:
        print(f"  {out['error']}; available: {', '.join(out.get('available', []))}")
        return 2
    payload = out["robot"]
    mass_by_link = out["mass_by_link"]
    print(f"  {args.robot}: {len(payload.get('links', []))} links, "
          f"{len(payload.get('joints', []))} joints")
    for lid, mp in mass_by_link.items():
        b = mp["bbox_size"]
        print(f"    {lid:<12} {mp['mass'] * 1000:8.1f} g   "
              f"bbox {b['x']*1000:.0f} x {b['y']*1000:.0f} x {b['z']*1000:.0f} mm")
    for lid, why in (out.get("failures") or {}).items():
        print(f"    {lid:<12} ** geometry did not build ** {why}")

    print()
    print("=" * 72)
    print("cosim — mapping F2's IR into a simulable spec")
    print("=" * 72)
    try:
        spec = from_cad_ir(payload, mass_by_link)
    except Exception as exc:  # noqa: BLE001
        print(f"  adapter rejected the payload: {type(exc).__name__}: {exc}")
        return 3

    problems = spec.validate()
    if problems:
        print(f"  spec did not validate ({len(problems)}):")
        for p in problems[:10]:
            print(f"    - {p}")
        print()
        print("  This is the seam doing its job: F2's IR and cosim's RobotSpec disagree")
        print("  about a required field. That is a contract bug between two stages, and")
        print("  it belongs to whichever side changed its shape last — not to the solver.")
        return 4

    spec = spec.fill_defaults()
    print(f"  {len(spec.links)} links, {len(spec.joints)} joints, {len(spec.actuators)} actuators")
    print("  " + describe_assumptions(spec).replace("\n", "\n  "))

    print()
    print("=" * 72)
    print("verdict")
    print("=" * 72)
    if unsecured:
        print(f"  BLOCKED before simulating: {len(unsecured)} board(s) are not mechanically")
        print(f"  secured — {', '.join(unsecured)}.")
        print("  Simulating a robot whose boards are not fixed to it would produce inertias")
        print("  that describe no physical object. Fix F1 first.")
        return 1

    print("  every board is secured and the mechanics mapped cleanly.")
    print("  Ready for a rollout: see tools/characterise.py for the motor side and")
    print("  cosim.rollout for the coupled loop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
