"""Tier-0 analytic criteria: <1ms, run on every candidate (§3).

Both criteria below follow §8's guidance to prefer ratios over booleans and
over angles: static_margin is a signed fraction of the support footprint's
half-extent, and mount_fits is a fraction of the smaller part's volume that
actually overlaps at a mounting joint. Neither needs contact simulation —
that's what makes them tier 0.
"""

from __future__ import annotations

import numpy as np

from engine.criteria.base import CriterionResult
from engine.criteria.registry import register
from engine.ir import RobotIR
from engine.kinematics import link_geometry_transform
from engine.mass_properties import MassProperties

# Engineering policy thresholds (not physical constants — provenance doesn't
# apply; these are requirements we chose, tunable as the product matures).
_STATIC_MARGIN_MIN = 0.10  # require CoM at least 10% of half-footprint inside the support edge
_MOUNT_OVERLAP_MIN = 1e-4  # require at least 0.01% volumetric overlap at a fixed joint


def _world_bbox(transform: np.ndarray, mp: MassProperties) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = mp.bbox_min.as_tuple(), mp.bbox_max.as_tuple()
    corners = np.array(
        [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    )
    world = corners @ transform[:3, :3].T + transform[:3, 3]
    return world.min(axis=0), world.max(axis=0)


@register("static_margin", tier=0)
def _static_margin(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    total_mass = 0.0
    world_com = np.zeros(3)
    per_link_bbox: list[tuple[np.ndarray, np.ndarray]] = []

    for link in ir.links:
        transform = link_geometry_transform(ir, link.id)
        mp = mass_props[link.id]
        local_com = np.array(mp.com.as_tuple())
        world_com_i = transform[:3, :3] @ local_com + transform[:3, 3]
        total_mass += mp.mass
        world_com += mp.mass * world_com_i
        per_link_bbox.append(_world_bbox(transform, mp))

    world_com /= total_mass

    # Support-polygon proxy: the links closest to the lowest Z plane in the
    # robot are the ones assumed to be touching the ground.
    global_min_z = min(wmin[2] for wmin, _ in per_link_bbox)
    ground_epsilon = 1e-3  # 1mm
    support = [(wmin, wmax) for wmin, wmax in per_link_bbox if wmin[2] <= global_min_z + ground_epsilon]

    fx_min = min(wmin[0] for wmin, _ in support)
    fx_max = max(wmax[0] for _, wmax in support)
    fy_min = min(wmin[1] for wmin, _ in support)
    fy_max = max(wmax[1] for _, wmax in support)

    cx, cy = world_com[0], world_com[1]
    dist_to_nearest_edge = min(cx - fx_min, fx_max - cx, cy - fy_min, fy_max - cy)
    half_extent = min(fx_max - fx_min, fy_max - fy_min) / 2.0
    magnitude = dist_to_nearest_edge / half_extent if half_extent > 0 else float("-inf")

    return [
        CriterionResult(
            name="static_margin",
            magnitude=magnitude,
            passed=bool(magnitude > _STATIC_MARGIN_MIN),
            unit="ratio",
            detail=(
                f"CoM=({cx:.4f},{cy:.4f})m support=[{fx_min:.4f},{fx_max:.4f}]x"
                f"[{fy_min:.4f},{fy_max:.4f}]m"
            ),
        )
    ]


@register("mount_fits", tier=0)
def _mount_fits(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    results: list[CriterionResult] = []
    for joint in (j for j in ir.joints if j.kind == "fixed"):
        parent_mp, child_mp = mass_props[joint.parent], mass_props[joint.child]
        p_lo, p_hi = _world_bbox(link_geometry_transform(ir, joint.parent), parent_mp)
        c_lo, c_hi = _world_bbox(link_geometry_transform(ir, joint.child), child_mp)

        overlap_dims = np.maximum(np.minimum(p_hi, c_hi) - np.maximum(p_lo, c_lo), 0.0)
        overlap_volume = float(np.prod(overlap_dims))
        ratio = overlap_volume / min(parent_mp.volume, child_mp.volume)

        results.append(
            CriterionResult(
                name=f"mount_fits[{joint.id}]",
                magnitude=ratio,
                passed=bool(ratio > _MOUNT_OVERLAP_MIN),
                unit="volume_ratio",
                detail=(
                    f"overlap={overlap_volume:.3e}m^3 parent_vol={parent_mp.volume:.3e}m^3 "
                    f"child_vol={child_mp.volume:.3e}m^3"
                ),
            )
        )
    return results
