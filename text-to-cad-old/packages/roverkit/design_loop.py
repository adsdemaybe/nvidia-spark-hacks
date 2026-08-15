"""
Closed-loop design refinement: iterate the robot's design until it passes in sim.

    reconfigure -> rebuild CAD -> export URDF -> load in MuJoCo -> run tests
         ^                                                            |
         +------------------- adjust design variables ----------------+

This is the harness the product needs: the success condition is defined as a set
of measurable criteria, and the loop searches the design space until every one
of them passes. Nothing here is hand-tuned — the starting design fails, and the
loop is what fixes it.

Criteria (all must pass):
    cad_builds      every part is a valid solid
    bay_clearance   the real electronics BOM fits with margin
    mechanics       joint/bearing/pin fits hold
    inertia_valid   every tensor positive-definite and obeys A+B>=C
    sim_loads       MuJoCo compiles the articulation with a floating base
    settles         dropped on a plane it lands upright and stops moving
    drives          wheel torque actually translates the base
    arm_holds       gravity torque at the shoulder is within motor limits
    payload         tip-over payload at full reach meets the target

Run:  .venv-cad/bin/python design_loop.py
"""

from __future__ import annotations

import math
import os
import tempfile
import time
from dataclasses import dataclass, field

import mujoco
import numpy as np
import yourdfpy

import export_sim as E
import rover_arm as R

TARGET_PAYLOAD = 0.500          # kg at full forward reach before tipping
SETTLE_TILT = math.radians(6)   # max roll/pitch once settled
DRIVE_MIN = 0.04                # m of travel under a 2 s torque command
MAX_ITERS = 40


# =============================================================================
# Criteria
# =============================================================================

@dataclass
class Check:
    name: str
    ok: bool
    value: float = 0.0
    target: float = 0.0
    note: str = ""

    def violation(self) -> float:
        """Normalised shortfall, 0 when satisfied."""
        if self.ok:
            return 0.0
        if self.target:
            return abs(self.target - self.value) / abs(self.target)
        return 1.0


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    payload: float = 0.0

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def score(self) -> float:
        return sum(c.violation() for c in self.checks)

    def failing(self) -> list[str]:
        return [c.name for c in self.checks if not c.ok]


# =============================================================================
# Simulation harness
# =============================================================================

def _compile_floating(urdf_path: str):
    """
    Load the URDF and give it a floating base plus a ground plane.

    MuJoCo welds a URDF root to the world, so without this the rover cannot
    move and its base mass is silently dropped from the model.
    """
    spec = mujoco.MjSpec.from_file(urdf_path)
    base = [b for b in spec.bodies if b.name == "base_link"][0]
    base.add_freejoint()
    spec.worldbody.add_geom(
        type=mujoco.mjtGeom.mjGEOM_PLANE, size=[5.0, 5.0, 0.1],
        pos=[0, 0, 0])
    return spec.compile()


def _spawn(model):
    d = mujoco.MjData(model)
    # Drop from just above the wheel contact height.
    clearance = (R.WHEEL_D / 2.0) * E.MM
    d.qpos[2] = clearance + 0.01
    mujoco.mj_forward(model, d)
    return d


def sim_checks(urdf_path: str) -> list[Check]:
    out = []
    try:
        model = _compile_floating(urdf_path)
        out.append(Check("sim_loads", True, note=f"{model.nbody} bodies"))
    except Exception as exc:
        return [Check("sim_loads", False, note=str(exc)[:90]),
                Check("settles", False), Check("drives", False)]

    # --- settle test ------------------------------------------------------
    d = _spawn(model)
    for _ in range(3000):                       # ~6 s at default timestep
        mujoco.mj_step(model, d)
    quat = d.qpos[3:7]
    w, x, y, z = quat
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    tilt = max(abs(roll), abs(pitch))
    moving = float(np.abs(d.qvel).max())
    stable = (not np.isnan(d.qpos).any()) and tilt < SETTLE_TILT and moving < 0.5
    out.append(Check("settles", stable, value=math.degrees(tilt),
                     target=math.degrees(SETTLE_TILT),
                     note=f"tilt {math.degrees(tilt):.1f}deg, |qvel| {moving:.2f}"))

    # --- drive test -------------------------------------------------------
    d = _spawn(model)
    for _ in range(1500):
        mujoco.mj_step(model, d)               # let it settle first
    x0 = float(d.qpos[0])
    axles = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, f"axle_{i}")
             for i in range(4)]
    dofs = [model.jnt_dofadr[a] for a in axles if a >= 0]
    for _ in range(1000):
        d.qfrc_applied[:] = 0.0
        for dof in dofs:
            d.qfrc_applied[dof] = E.NEMA_TORQUE * 0.6
        mujoco.mj_step(model, d)
    d.qfrc_applied[:] = 0.0
    travel = abs(float(d.qpos[0]) - x0)
    out.append(Check("drives", travel > DRIVE_MIN, value=travel,
                     target=DRIVE_MIN, note=f"{travel * 1000:.0f} mm"))
    return out


# =============================================================================
# Static analysis
# =============================================================================

def static_checks(urdf_path: str, links) -> tuple[list[Check], float]:
    r = yourdfpy.URDF.load(urdf_path)
    mass = {lk.name: E.combine(lk.solids, lk.points) for lk in links}

    def com_at(cfg):
        r.update_cfg(cfg)
        M, acc = 0.0, np.zeros(3)
        for n, it in mass.items():
            T = r.get_transform(n, "base_link")
            acc += it.mass * (T[:3, :3] @ it.com + T[:3, 3])
            M += it.mass
        return acc / M, M

    reach_cfg = {"shoulder": 0.0, "elbow": 0.0}
    com, M = com_at(reach_cfg)
    gx = r.get_transform("gripper_body", "base_link")[0, 3]
    xw = R.AXLE_X * E.MM
    payload = M * (xw - com[0]) / (gx - xw) if gx > xw else 0.0
    payload = max(payload, 0.0)

    # Gravity torque about the shoulder with the arm held straight out.
    tau, xs = 0.0, r.get_transform("link_shoulder", "base_link")[0, 3]
    for n in ("link_shoulder", "link_elbow", "gripper_body", "jaw_a", "jaw_b"):
        it = mass[n]
        T = r.get_transform(n, "base_link")
        p = T[:3, :3] @ it.com + T[:3, 3]
        tau += it.mass * 9.81 * abs(p[0] - xs)

    # Available shoulder torque from the selected motor, after any reduction.
    # Efficiency only applies when a gearbox is actually fitted.
    motor_t = R.MOTORS[R.SHOULDER_MOTOR]["torque"]
    avail = motor_t * (R.SHOULDER_GEAR * R.GEARBOX_EFF
                       if R.SHOULDER_GEAR > 1.5 else 1.0)
    # Gearbox backlash shows up as end-effector slop over the arm's reach.
    reach_mm = (gx - r.get_transform("link_shoulder", "base_link")[0, 3]) * 1000.0
    slop = abs(reach_mm) * math.radians(R.GEAR_BACKLASH_DEG[R.SHOULDER_GEAR])
    return ([
        Check("arm_holds", tau <= avail, value=tau, target=avail,
              note=f"{tau:.2f} N.m needed vs {avail:.2f} from "
                   f"{R.SHOULDER_MOTOR} @ {R.SHOULDER_GEAR:.2f}:1"),
        Check("backlash", slop <= R.MAX_BACKLASH_MM, value=slop,
              target=R.MAX_BACKLASH_MM,
              note=f"{slop:.1f} mm end-effector slop"),
        Check("payload", payload >= TARGET_PAYLOAD, value=payload,
              target=TARGET_PAYLOAD, note=f"{payload * 1000:.0f} g"),
    ], payload)


# =============================================================================
# Full evaluation of one design
# =============================================================================

def evaluate(design: dict, workdir: str) -> Report:
    rep = Report()
    try:
        R.reconfigure(**design)
    except Exception as exc:
        rep.checks.append(Check("cad_builds", False, note=f"reconfigure: {exc}"))
        return rep

    try:
        links, joints = E.build_model()
        bad = [lk.name for lk in links
               for p, _ in lk.solids if not p.is_valid]
        rep.checks.append(Check("cad_builds", not bad,
                                note=", ".join(bad) if bad else "all solids valid"))
    except Exception as exc:
        rep.checks.append(Check("cad_builds", False, note=str(exc)[:90]))
        return rep

    bay = R.check_electronics_bay()
    rep.checks.append(Check("bay_clearance", not bay,
                            note=bay[0] if bay else "fits"))
    mech = R.check_mechanics()
    rep.checks.append(Check("mechanics", not mech, note=mech[0] if mech else "ok"))
    mounts = R.check_mount_fit()
    rep.checks.append(Check("mount_fits", not mounts,
                            note=mounts[0] if mounts else
                            "all motor faces land on material"))
    _f = R.check_lateral_stability()
    _v, _t = R.metric_lateral_stability()
    rep.checks.append(Check("lateral_stability", not _f, value=_v, target=_t,
                            note=_f[0] if _f else f"{_v:.1f} vs {_t:.1f}"))
    _f = R.check_grip_aperture()
    _v, _t = R.metric_grip_aperture()
    rep.checks.append(Check("grip_aperture", not _f, value=_v, target=_t,
                            note=_f[0] if _f else f"{_v:.1f} vs {_t:.1f}"))

    worst = 0.0
    for lk in links:
        ev = np.linalg.eigvalsh(E.combine(lk.solids, lk.points).tensor)
        a, b, c = sorted(ev)
        worst = max(worst, -a, c - (a + b))
    rep.checks.append(Check("inertia_valid", worst <= 0,
                            note=f"worst margin {worst:.2e}"))

    urdf = os.path.join(workdir, "candidate.urdf")
    E.write_urdf(links, joints, urdf, visuals=False)

    stat, payload = static_checks(urdf, links)
    rep.checks.extend(stat)
    rep.payload = payload
    rep.checks.extend(sim_checks(urdf))
    return rep


# =============================================================================
# Search
# =============================================================================

def objective(rep: Report) -> float:
    """Lower is better. Zero means every criterion passes."""
    return rep.score


def refine(start: dict, workdir: str, max_iters: int = MAX_ITERS):
    """
    Coordinate descent over the design variables.

    Deliberately simple and deterministic: each iteration tries one step per
    variable in both directions and keeps the best improvement. In the product
    this is where a learned or LLM-driven proposal step would slot in — the
    harness around it, and the success condition, are the same.
    """
    design = dict(start)
    rep = evaluate(design, workdir)
    history = [(0, dict(design), rep)]
    print(f"iter  0  score {rep.score:7.3f}  payload {rep.payload*1000:6.0f} g  "
          f"failing: {rep.failing() or 'none'}")

    step = {k: (hi - lo) * 0.25 for k, (lo, hi) in R.DESIGN_VARS.items()}
    it = 0
    while it < max_iters:
        it += 1
        best, best_rep, best_design = objective(rep), rep, None

        # Discrete catalogue choices: motor and gearbox ratio.
        for motor in R.MOTOR_BY_TORQUE:
            for gear in R.GEAR_OPTIONS:
                if motor == R.SHOULDER_MOTOR and gear == R.SHOULDER_GEAR:
                    continue
                cand = dict(design)
                cand["SHOULDER_MOTOR"] = motor
                cand["SHOULDER_GEAR"] = gear
                cr = evaluate(cand, workdir)
                better = (objective(cr) < best - 1e-9) or (
                    objective(cr) <= 1e-9 and best <= 1e-9
                    and cr.payload > best_rep.payload + 1e-4)
                if better:
                    best, best_rep, best_design = objective(cr), cr, cand

        for var, (lo, hi) in R.DESIGN_VARS.items():
            for direction in (+1, -1):
                cand = dict(design)
                cand[var] = min(hi, max(lo, cand[var] + direction * step[var]))
                if abs(cand[var] - design[var]) < 1e-9:
                    continue
                cr = evaluate(cand, workdir)
                # Primary: satisfy every criterion. Secondary: more payload.
                better = (objective(cr) < best - 1e-9) or (
                    objective(cr) <= 1e-9 and best <= 1e-9
                    and cr.payload > best_rep.payload + 1e-4)
                if better:
                    best, best_rep, best_design = objective(cr), cr, cand

        if best_design is None:
            step = {k: v * 0.5 for k, v in step.items()}
            if max(step.values()) < 1e-3:
                break
            continue

        design, rep = best_design, best_rep
        history.append((it, dict(design), rep))
        print(f"iter {it:2d}  score {rep.score:7.3f}  payload {rep.payload*1000:6.0f} g  "
              f"failing: {rep.failing() or 'none'}")
        if rep.passed and rep.payload >= TARGET_PAYLOAD * 1.15:
            break

    return design, rep, history


if __name__ == "__main__":
    work = tempfile.mkdtemp(prefix="design_loop_")
    t0 = time.time()
    start = R.current_design()

    print("=== baseline ===")
    base_rep = evaluate(start, work)
    for c in base_rep.checks:
        print(f"  {'PASS' if c.ok else 'FAIL'}  {c.name:14} {c.note}")
    print(f"  baseline payload {base_rep.payload*1000:.0f} g\n")

    print("=== refining ===")
    design, rep, history = refine(start, work)

    print("\n=== final ===")
    for c in rep.checks:
        print(f"  {'PASS' if c.ok else 'FAIL'}  {c.name:14} {c.note}")
    print("\ndesign variables:")
    for k in R.DESIGN_VARS:
        print(f"  {k:20} {start[k]:8.2f}  ->  {design[k]:8.2f}")
    print(f"\npayload {base_rep.payload*1000:.0f} g -> {rep.payload*1000:.0f} g "
          f"({len(history)} accepted steps, {time.time()-t0:.0f}s)")

    if rep.passed:
        print("\nSUCCESS — writing the converged design")
        R.reconfigure(**design)
        links, joints = E.build_model()
        os.makedirs(E.SIM_DIR, exist_ok=True)
        E.write_meshes(links)
        E.write_urdf(links, joints, os.path.join(E.SIM_DIR, "rover.urdf"))
        E.write_srdf(joints, os.path.join(E.SIM_DIR, "rover.srdf"))
        usd = os.path.join(E.SIM_DIR, "rover.usda")
        if os.path.exists(usd):
            os.remove(usd)
        E.write_usd(links, joints, usd)
        with open(os.path.join(E.SIM_DIR, "design.json"), "w") as f:
            import json
            json.dump(design, f, indent=2)
        print("wrote sim/ + sim/design.json")
    else:
        print(f"\nDID NOT CONVERGE — still failing: {rep.failing()}")
