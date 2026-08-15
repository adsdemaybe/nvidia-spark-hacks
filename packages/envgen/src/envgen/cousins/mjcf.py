"""Scene -> MJCF. The validator's view of the world, and the settle harness.

MuJoCo is the CPU physics oracle: penetration depths, settle deltas, terminal
velocities -- in seconds, no Kit boot. Collision geometry is the asset's convex
hull set (MuJoCo convexifies mesh geoms anyway, which is exactly why assetize
decomposes concave objects first).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from r2s.core import Pose
from r2s.core.schemas import AssetPayload, Placement, ShellPayload


def build_mjcf(
    shell: ShellPayload,
    placements: list[Placement],
    assets: dict[str, AssetPayload],
    fetch_collision,  # (asset_id, hull_index) -> Path of an OBJ on disk
    out_path: Path,
) -> Path:
    root = ET.Element("mujoco", model="cousin_scene")
    ET.SubElement(root, "option", timestep="0.008333", integrator="implicitfast")
    ET.SubElement(root, "compiler", angle="radian", meshdir=".")

    asset_el = ET.SubElement(root, "asset")
    world = ET.SubElement(root, "worldbody")

    # floor
    ET.SubElement(world, "geom", name="floor", type="plane", size="20 20 0.1",
                  friction="0.8 0.005 0.0001", rgba="0.55 0.55 0.6 1")

    # walls as thin static boxes along fitted planes (visual context + containment)
    for i, w in enumerate(shell.walls):
        n = np.asarray(w.normal[:2])
        if np.linalg.norm(n) < 1e-6:
            continue
        n = n / np.linalg.norm(n)
        cx, cy = n * w.offset
        yaw = float(np.arctan2(n[1], n[0]))
        h = shell.room_height_m.value if shell.room_height_m else 2.5
        ET.SubElement(
            world, "geom", name=f"wall_{i}", type="box",
            size=f"0.02 4.0 {h/2:.3f}",
            pos=f"{cx:.4f} {cy:.4f} {h/2:.3f}",
            quat=f"{np.cos(yaw/2):.6f} 0 0 {np.sin(yaw/2):.6f}",
            rgba="0.7 0.7 0.75 0.35",
        )

    mesh_files: dict[str, str] = {}
    for pl in placements:
        a = assets[pl.asset_id]
        body = ET.SubElement(
            world, "body", name=pl.instance_id,
            pos=_v3(pl.pose.position), quat=_q4(pl.pose.quaternion),
        )
        if pl.role != "fixture":
            ET.SubElement(body, "freejoint", name=f"{pl.instance_id}_free")
        ET.SubElement(
            body, "inertial",
            pos=_v3(a.physics.com_local.value),
            mass=f"{a.physics.mass.value:.6f}",
            diaginertia=_v3(a.physics.inertia_diag.value),
        )
        mu = a.physics.static_friction.value
        for hi in range(a.collision.n_hulls):
            key = f"{a.asset_id}_h{hi}"
            if key not in mesh_files:
                p = fetch_collision(a.asset_id, hi)
                ET.SubElement(asset_el, "mesh", name=key, file=str(p))
                mesh_files[key] = str(p)
            ET.SubElement(
                body, "geom", type="mesh", mesh=key,
                friction=f"{mu:.3f} 0.005 0.0001",
                rgba="0.8 0.6 0.4 1" if pl.role == "target" else "0.6 0.6 0.65 1",
            )

    ET.indent(root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="unicode")
    return out_path


@dataclass
class SettleOutcome:
    settled: dict[str, Pose]
    max_translation_m: float
    max_rotation_deg: float
    max_terminal_speed: float
    max_penetration_m: float
    exploded: bool
    steps: int
    dt: float


def settle(mjcf_path: Path, movable_ids: list[str], *, steps: int = 240) -> SettleOutcome:
    """240 steps @ 1/120 = 2.0s sim time. Accept criteria live in the gate; this
    just measures. Also bakes: the caller writes settled poses back into the
    scene so the ARTIFACT is deterministic even though PhysX/MuJoCo is not
    bitwise stable across machines."""
    import mujoco

    model = mujoco.MjModel.from_xml_path(str(mjcf_path))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)

    def pose_of(name: str) -> tuple[np.ndarray, np.ndarray]:
        bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
        return data.xpos[bid].copy(), data.xquat[bid].copy()

    start = {m: pose_of(m) for m in movable_ids}

    max_pen = 0.0
    exploded = False
    for _ in range(steps):
        mujoco.mj_step(model, data)
        if data.ncon:
            max_pen = max(max_pen, float(-data.contact.dist.min()))
        if not np.isfinite(data.qpos).all() or np.abs(data.qvel).max() > 50.0:
            exploded = True
            break

    settled: dict[str, Pose] = {}
    max_t, max_r, max_v = 0.0, 0.0, 0.0
    for m in movable_ids:
        p0, q0 = start[m]
        p1, q1 = pose_of(m)
        max_t = max(max_t, float(np.linalg.norm(p1 - p0)))
        dq = abs(float(np.clip(np.abs(q0 @ q1), -1, 1)))
        max_r = max(max_r, float(np.degrees(2 * np.arccos(dq))))
        settled[m] = Pose(position=tuple(float(v) for v in p1),
                          quaternion=tuple(float(v) for v in q1))
    if len(data.qvel):
        max_v = float(np.abs(data.qvel).max())

    return SettleOutcome(
        settled=settled, max_translation_m=max_t, max_rotation_deg=max_r,
        max_terminal_speed=max_v, max_penetration_m=max_pen,
        exploded=exploded, steps=steps, dt=float(model.opt.timestep),
    )


def _v3(v) -> str:
    return f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}"


def _q4(q) -> str:
    return f"{q[0]:.6f} {q[1]:.6f} {q[2]:.6f} {q[3]:.6f}"
