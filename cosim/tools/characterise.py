#!/usr/bin/env python3
"""Characterise a drive, validate the surface, and run a closed-loop rollout.

    python tools/characterise.py --grid            # build and validate the surface
    python tools/characterise.py --rollout         # close the loop using it

Splitting characterisation from the rollout is the whole performance argument: the grid
costs one ngspice run per point, once, and the rollout then costs interpolation per
period. Re-solving the same circuit every timestep would spend the entire budget in
process startup.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from cosim.electrical import SpiceBackend, Surface
from cosim.robot import one_joint_arm
from cosim.rollout import SurfaceModel, rollout

HERE = Path(__file__).resolve().parent.parent
SURFACE_PATH = HERE / "runs" / "surface.json"


def build(args: argparse.Namespace) -> Surface:
    backend = SpiceBackend(motor=args.motor, freq_hz=args.freq)
    duties = [round(x / (args.duty_steps - 1), 4) for x in range(args.duty_steps)]
    # ω up to a little past no-load, so the rollout never has to extrapolate.
    omegas = [round(args.omega_max * x / (args.omega_steps - 1), 2) for x in range(args.omega_steps)]

    print(f"characterising {args.motor}: {len(duties)}x{len(omegas)} = {len(duties)*len(omegas)} points")
    surface = Surface.characterise(backend, duties, omegas)
    SURFACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SURFACE_PATH.write_text(surface.to_json())
    print(f"surface -> {SURFACE_PATH}")

    # Validate strictly between grid nodes. Checking at the nodes would only prove the
    # table round-tripped, which is not the question being asked.
    mid_d = [(duties[i] + duties[i + 1]) / 2 for i in range(len(duties) - 1)][:3]
    mid_w = [(omegas[i] + omegas[i + 1]) / 2 for i in range(len(omegas) - 1)][:3]
    samples = [(d, w) for d in mid_d for w in mid_w]
    print(f"\nvalidating against ngspice at {len(samples)} off-grid points …")
    report = surface.validate(backend, samples)
    for s in report["samples"]:
        print(
            f"  duty {s['duty']:.3f} ω {s['omega']:7.1f}  "
            f"exact {s['exact_torque_nm']*1000:7.3f} mN·m   "
            f"surface {s['surface_torque_nm']*1000:7.3f} mN·m   "
            f"abs {s['abs_error_nm']*1000:6.3f} mN·m  "
            f"({s['full_scale_error']*100:5.2f}% of full scale)"
        )
    print(
        f"\nfull-scale torque: {report['full_scale_torque_nm']*1000:.2f} mN·m"
        f"\nworst error vs full scale: {report['max_full_scale_error']*100:.2f}%"
        f"  at {report['worst_at']}"
        f"\n(worst *relative* error {report['max_rel_error']*100:.0f}% — near the conduction"
        f" threshold, where torque is ~0 and a relative figure is meaningless)"
    )
    (SURFACE_PATH.parent / "surface-validation.json").write_text(json.dumps(report, indent=2))
    return surface


def run_rollout(args: argparse.Namespace) -> None:
    if not SURFACE_PATH.exists():
        raise SystemExit("no surface yet — run with --grid first")
    surface = Surface.from_json(SURFACE_PATH.read_text())
    spec = one_joint_arm(gear_ratio=args.gear)
    print(f"rollout: {spec.name}, gear {args.gear}:1, {args.periods} periods at {args.dt*1000:.1f} ms")

    result = rollout(
        spec,
        SurfaceModel(surface),
        periods=args.periods,
        dt_s=args.dt,
        duty_of=lambda seq, t, angle: args.duty,
    )
    print()
    print(result.summary())

    if result.frames:
        print("\n  seq      t     duty   ω shaft    current    torque    joint")
        step = max(1, len(result.frames) // 10)
        for f in result.frames[::step]:
            print(
                f"  {f.seq:4d} {f.t:6.3f}s   {f.duty:4.2f} {f.omega_shaft_rad_s:8.1f} "
                f"{f.current_a:8.3f} A {f.torque_nm*1000:8.2f} mN·m {math.degrees(f.joint_angle_rad):+8.1f}°"
            )

    out = SURFACE_PATH.parent / "rollout.json"
    out.write_text(json.dumps([f.__dict__ for f in result.frames], indent=2))
    print(f"\nframes -> {out}")
    raise SystemExit(1 if result.diverged else 0)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--grid", action="store_true", help="characterise and validate")
    p.add_argument("--rollout", action="store_true", help="closed-loop rollout")
    p.add_argument("--motor", default="n20-6v")
    p.add_argument("--freq", type=float, default=20000.0)
    p.add_argument("--duty-steps", type=int, default=5)
    p.add_argument("--omega-steps", type=int, default=5)
    p.add_argument("--omega-max", type=float, default=1400.0)
    p.add_argument("--gear", type=float, default=100.0)
    p.add_argument("--duty", type=float, default=1.0)
    p.add_argument("--periods", type=int, default=400)
    p.add_argument("--dt", type=float, default=0.001)
    args = p.parse_args()

    if args.grid:
        build(args)
    if args.rollout:
        run_rollout(args)
    if not args.grid and not args.rollout:
        p.error("pass --grid, --rollout, or both")


if __name__ == "__main__":
    main()
