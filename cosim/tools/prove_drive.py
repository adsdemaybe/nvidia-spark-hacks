"""Prove the electrical side is what moves the joint, rather than merely coexisting with it.

    python tools/prove_drive.py

A rollout already reports current and joint angle in the same frame. That is not proof of
anything: two numbers advancing together is exactly what you would see if the mechanics
were being driven by a constant nobody had noticed, and the current column were decoration.
The co-simulation would look identical.

So this does not report a correlation, it runs an **ablation**. Series resistance is added
between the supply and the motor — which is what a real board contributes: copper, a
connector, an H-bridge's R_ds(on) — and the mechanics is then asked what it did. If the
electrical path is genuinely driving the joint, then across increasing resistance:

    current falls  ->  torque falls  ->  the joint gets slower and travels less

and every one of those is measured, not asserted. If instead the joint travels the same
distance regardless of what the electrical model says, the coupling is decorative and this
prints FAILED. That is the outcome worth designing for; a proof that cannot fail is not one.

The resistances are not arbitrary either. 0 ohm is the ideal board, 0.2 ohm is roughly what
rover-motor-driver's own copper plus a DRV8833 channel contributes (plan: 0.72 ohm per
channel, two channels in parallel paths), and the larger values are the same board built
badly — thin traces, a poor connector, a long harness.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))

from cosim.electrical import Surface  # noqa: E402
from cosim.robot import one_joint_arm  # noqa: E402
from cosim.rollout import rollout  # noqa: E402

SURFACE_PATH = HERE / "runs" / "surface.json"


class SeriesResistance:
    """The characterised drive, with extra resistance in front of it.

    Wrapping the surface rather than re-running ngspice per point keeps this cheap enough
    to sweep. The model is the one the transient sim already uses: the winding sees
    `V - I*R_extra`, so at a given duty and speed the operating point slides down the
    surface exactly as if the supply had sagged.
    """

    def __init__(self, surface: Surface, r_extra_ohm: float, v_supply: float = 7.4):
        self.surface = surface
        self.r = r_extra_ohm
        self.v = v_supply

    def evaluate(self, duty: float, omega_rad_s: float):
        sign = 1.0 if omega_rad_s >= 0 else -1.0
        op = self.surface.evaluate(duty, abs(omega_rad_s))
        if self.r <= 0:
            return op if sign > 0 else _flip(op)
        # One fixed-point pass: the drop depends on the current, which depends on the
        # drop. It converges immediately at these magnitudes and a second pass moves the
        # answer by well under a percent, so iterating further would be theatre.
        drop = abs(op.current_avg_a) * self.r
        derated = max(0.0, (self.v - drop) / self.v)
        op2 = self.surface.evaluate(duty * derated, abs(omega_rad_s))
        return op2 if sign > 0 else _flip(op2)


def _flip(op):
    from dataclasses import replace

    return replace(op, torque_nm=-op.torque_nm, current_avg_a=-op.current_avg_a)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--periods", type=int, default=400)
    ap.add_argument("--dt", type=float, default=0.001)
    ap.add_argument("--duty", type=float, default=1.0)
    ap.add_argument("--gear", type=float, default=100.0)
    ap.add_argument("--json", help="write the table here")
    a = ap.parse_args()

    if not SURFACE_PATH.exists():
        print("no characterised surface — run: python tools/characterise.py --grid")
        return 2
    surface = Surface.from_json(SURFACE_PATH.read_text())

    print("Does the electrical path actually move the joint?")
    print()
    print("  Adding series resistance between supply and motor — copper, connector,")
    print("  H-bridge. If the coupling is real, current falls and the joint travels less.")
    print()
    print(f"  {'R series':>9}  {'peak I':>8}  {'|torque|':>9}  {'joint travel':>13}  {'final ω':>9}")
    print("  " + "-" * 58)

    rows = []
    for r_ohm in (0.0, 0.2, 0.5, 1.0, 2.0, 4.0):
        spec = one_joint_arm(gear_ratio=a.gear)
        res = rollout(
            spec, SeriesResistance(surface, r_ohm),
            periods=a.periods, dt_s=a.dt,
            duty_of=lambda seq, t, angle: a.duty,
        )
        if res.diverged:
            print(f"  {r_ohm:7.2f} Ω   diverged — {res.divergence_reason[:40]}")
            continue
        last = res.frames[-1]
        peak_tq = max(abs(f.torque_nm) for f in res.frames)
        travel = abs(last.joint_angle_rad)
        rows.append({"r_ohm": r_ohm, "peak_current_a": res.peak_current_a,
                     "peak_torque_nm": peak_tq, "travel_rad": travel,
                     "final_omega_rad_s": last.omega_shaft_rad_s})
        print(f"  {r_ohm:7.2f} Ω  {res.peak_current_a:7.3f} A  {peak_tq:8.4f} N·m"
              f"  {travel:10.3f} rad  {last.omega_shaft_rad_s:8.1f}")

    print()
    if len(rows) < 3:
        print("  INCONCLUSIVE — too few rollouts survived to compare.")
        return 1

    # The claim under test, stated so it can fail: each of these must fall as resistance
    # rises. Monotonic rather than merely "different", because a coupling that responds
    # in the wrong direction is worse than one that does not respond at all.
    checks = []
    for key, label in (("peak_current_a", "current"), ("peak_torque_nm", "torque"),
                       ("travel_rad", "joint travel")):
        vals = [r[key] for r in rows]
        mono = all(b <= a_ + 1e-9 for a_, b in zip(vals, vals[1:]))
        span = (vals[0] - vals[-1]) / vals[0] * 100 if vals[0] else 0.0
        checks.append((label, mono, span))
        print(f"  {'PASS' if mono else '** FAIL **'}  {label} falls monotonically with "
              f"resistance  ({span:+.1f}% from first to last)")

    moved = rows[0]["travel_rad"] > 1e-3
    print(f"  {'PASS' if moved else '** FAIL **'}  the joint actually moved at 0 Ω "
          f"({rows[0]['travel_rad']:.3f} rad)")

    ok = moved and all(m for _, m, _ in checks)
    print()
    if ok:
        print("  PROVEN: changing only the electrical path changes the motion, in the")
        print("  direction the physics requires. The current column is driving the joint,")
        print("  not accompanying it.")
    else:
        print("  NOT PROVEN: the mechanics did not respond to the electrical change as")
        print("  physics requires. Treat the coupling as decorative until this passes.")

    if a.json:
        Path(a.json).write_text(json.dumps({"rows": rows, "proven": ok}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
