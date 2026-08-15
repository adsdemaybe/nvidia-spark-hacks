"""Scoring harness and refinement loop for the quadruped.

Deliberately mirrors roverkit.design_loop: same Check/Report shapes, same
"criteria decide" rule, same coordinate descent plus discrete catalogue search.
The criteria themselves are entirely different, because a quadruped fails in
ways a wheeled rover cannot.
"""
from __future__ import annotations
import math, os, sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quadruped as Q

MASS_BUDGET = 12.0          # kg; above this it is not a benchtop machine
MAX_ITERS = 30


@dataclass
class Check:
    name: str; ok: bool; value: float = 0.0; target: float = 0.0; note: str = ""
    def violation(self) -> float:
        if self.ok: return 0.0
        return abs(self.target - self.value) / abs(self.target) if self.target else 1.0


@dataclass
class Report:
    checks: list = field(default_factory=list)
    mass: float = 0.0
    @property
    def passed(self): return all(c.ok for c in self.checks)
    @property
    def score(self): return sum(c.violation() for c in self.checks)
    def failing(self): return [c.name for c in self.checks if not c.ok]


def evaluate(design: dict) -> Report:
    rep = Report()
    try:
        Q.reconfigure(**design)
    except Exception as exc:
        rep.checks.append(Check("cad_builds", False, note=f"reconfigure: {exc}"))
        return rep

    try:
        parts = [Q.build_body(), Q.build_hip_bracket(),
                 Q.build_leg_segment(Q.UPPER_LEG, "u"),
                 Q.build_leg_segment(Q.LOWER_LEG, "l"), Q.build_foot()]
        bad = [p.label for p in parts if not p.is_valid]
        rep.checks.append(Check("cad_builds", not bad,
                                note=", ".join(bad) if bad else "all solids valid"))
        body = parts[0].volume * Q.RHO_PLA
        leg = sum(p.volume * Q.RHO_PLA for p in parts[1:])
        motors = 4 * (Q.MOTORS[Q.KNEE_MOTOR]["mass"] + Q.MOTORS[Q.HIP_MOTOR]["mass"]
                      + Q.MOTORS[Q.ABDUCT_MOTOR]["mass"])
        mass = body + 4 * leg + motors + Q.PAYLOAD_M
    except Exception as exc:
        rep.checks.append(Check("cad_builds", False, note=str(exc)[:90]))
        return rep
    rep.mass = mass

    mf = Q.check_mount_fit()
    rep.checks.append(Check("mount_fits", not mf, note=mf[0] if mf else "all motor faces land on material"))
    rc = Q.check_reach()
    rep.checks.append(Check("reach", not rc, note=rc[0] if rc else "stance and step reachable"))
    st = Q.check_stability()
    rep.checks.append(Check("stability", not st, note=st[0] if st else
                            f"support margin {Q.support_polygon_margin():.0f} mm"))

    t = Q.joint_torques()
    for tag in ("stand", "reach"):
        vals = t.get(tag)
        if vals is None:
            rep.checks.append(Check(f"torque_{tag}", False, note="unreachable stance"))
            continue
        for joint, motor in (("hip", Q.HIP_MOTOR), ("knee", Q.KNEE_MOTOR)):
            avail = Q.MOTORS[motor]["torque"] * (Q.GEAR if Q.GEAR > 1.5 else 1.0)
            need = vals[joint]
            rep.checks.append(Check(f"{joint}_{tag}", need <= avail, value=need,
                                    target=avail,
                                    note=f"{need:.2f} N.m vs {avail:.2f} from {motor}"))

    rep.checks.append(Check("mass_budget", mass <= MASS_BUDGET, value=mass,
                            target=MASS_BUDGET, note=f"{mass:.2f} kg"))
    return rep


def refine(start: dict, max_iters: int = MAX_ITERS):
    design = dict(start)
    rep = evaluate(design)
    hist = [(0, dict(design), rep)]
    print(f"iter  0  score {rep.score:7.3f}  mass {rep.mass:5.2f} kg  failing: {rep.failing() or 'none'}")
    step = {k: (hi - lo) * 0.25 for k, (lo, hi) in Q.DESIGN_VARS.items()}
    it = 0
    while it < max_iters:
        it += 1
        best, best_rep, best_design = rep.score, rep, None
        for var, opts in Q.DISCRETE_VARS.items():
            for choice in opts:
                if design.get(var) == choice: continue
                cand = dict(design); cand[var] = choice
                cr = evaluate(cand)
                if cr.score < best - 1e-9 or (cr.score <= 1e-9 and best <= 1e-9 and cr.mass < best_rep.mass - 1e-4):
                    best, best_rep, best_design = cr.score, cr, cand
        for var, (lo, hi) in Q.DESIGN_VARS.items():
            for d in (+1, -1):
                cand = dict(design)
                cand[var] = min(hi, max(lo, cand[var] + d * step[var]))
                if abs(cand[var] - design[var]) < 1e-9: continue
                cr = evaluate(cand)
                if cr.score < best - 1e-9 or (cr.score <= 1e-9 and best <= 1e-9 and cr.mass < best_rep.mass - 1e-4):
                    best, best_rep, best_design = cr.score, cr, cand
        if best_design is None:
            step = {k: v * 0.5 for k, v in step.items()}
            if max(step.values()) < 1e-3: break
            continue
        design, rep = best_design, best_rep
        hist.append((it, dict(design), rep))
        print(f"iter {it:2d}  score {rep.score:7.3f}  mass {rep.mass:5.2f} kg  failing: {rep.failing() or 'none'}")
        if rep.passed: break
    return design, rep, hist


if __name__ == "__main__":
    start = Q.current_design()
    print("=== baseline ===")
    r0 = evaluate(start)
    for c in r0.checks:
        print(f"  {'PASS' if c.ok else 'FAIL'}  {c.name:12} {c.note}")
    print(f"\n=== refining ===")
    design, rep, hist = refine(start)
    print("\n=== final ===")
    for c in rep.checks:
        print(f"  {'PASS' if c.ok else 'FAIL'}  {c.name:12} {c.note}")
    print("\ndesign:")
    for k, v in design.items():
        print(f"  {k:16} {v}")
    print(f"\n{'CONVERGED' if rep.passed else 'DID NOT CONVERGE'} in {len(hist)-1} accepted steps")
