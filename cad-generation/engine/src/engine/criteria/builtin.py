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
from engine.kinematics import link_frames, link_geometry_transform
from engine.mass_properties import MassProperties

# Engineering policy thresholds (not physical constants — provenance doesn't
# apply; these are requirements we chose, tunable as the product matures).
_STATIC_MARGIN_MIN = 0.10  # require CoM at least 10% of half-footprint inside the support edge
_MOUNT_OVERLAP_MIN = 1e-4  # require at least 0.01% volumetric overlap at a fixed joint

# A degenerate support polygon (all support links collapsing to a line or point)
# has no half-extent to normalise by. The honest magnitude is "infinitely bad",
# but `float("-inf")` serialises as bare `-Infinity`, which is not valid JSON and
# is rejected by strict parsers — including `JSON.parse` on the TypeScript half of
# the §6 contract. A large finite sentinel keeps the report machine-readable and
# still sorts below every real ratio.
_DEGENERATE = -1.0e6


def _world_bbox(transform: np.ndarray, mp: MassProperties) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = mp.bbox_min.as_tuple(), mp.bbox_max.as_tuple()
    corners = np.array(
        [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    )
    world = corners @ transform[:3, :3].T + transform[:3, 3]
    return world.min(axis=0), world.max(axis=0)


@register("static_margin", tier=0)
def _static_margin(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    # A bolted-down robot cannot tip, so there is no margin to measure. Returning
    # nothing is right where returning a failure would be actively wrong: a bench
    # arm reaching past its own base plate is doing its job, and no arm that can
    # reach anything would ever pass.
    if ir.base == "fixed":
        return []

    total_mass = 0.0
    world_com = np.zeros(3)
    per_link_bbox: list[tuple[np.ndarray, np.ndarray]] = []

    frames = link_frames(ir)
    for link in ir.links:
        transform = link_geometry_transform(ir, link.id, frames)
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
    degenerate = half_extent <= 0
    magnitude = dist_to_nearest_edge / half_extent if not degenerate else _DEGENERATE

    return [
        CriterionResult(
            name="static_margin",
            magnitude=magnitude,
            passed=bool(magnitude > _STATIC_MARGIN_MIN),
            unit="ratio",
            detail=(
                f"CoM=({cx:.4f},{cy:.4f})m support=[{fx_min:.4f},{fx_max:.4f}]x"
                f"[{fy_min:.4f},{fy_max:.4f}]m"
                + (" (degenerate support polygon: no half-extent to normalise by)" if degenerate else "")
            ),
        )
    ]


@register("inertia_valid", tier=0)
def _inertia_valid(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    """Every link's inertia tensor must be physically realisable.

    Two conditions, both on the principal moments A <= B <= C: all positive,
    and the triangle inequality A + B >= C. No rigid body can violate either,
    so a failure here is a bug in the mass model — never a fact about the
    design, and never something the optimizer should try to search its way out
    of. It sits at tier 0 because tier 2 cannot run without it: MuJoCo rejects
    a non-realisable inertia at model-compile time, and the error it raises
    names the body, not the reason.

    Reported as a margin normalised by C, so it stays comparable across links
    that differ by orders of magnitude in size — §8's "prefer ratios".
    """
    results: list[CriterionResult] = []
    for link in ir.links:
        a, b, c = mass_props[link.id].inertia.principal_moments()
        # Worst of the two violations, normalised. Negative is good.
        scale = abs(c) if c != 0 else 1.0
        worst = max(-a, c - (a + b)) / scale
        results.append(
            CriterionResult(
                name=f"inertia_valid[{link.id}]",
                magnitude=float(worst),
                passed=bool(worst <= 0),
                unit="ratio",
                detail=(
                    f"principal moments ({a:.4e}, {b:.4e}, {c:.4e}) kg*m^2; "
                    + ("positive-definite and A+B>=C" if worst <= 0
                       else "not physically realisable — the mass model is wrong")
                ),
            )
        )
    return results


@register("mount_fits", tier=0)
def _mount_fits(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    results: list[CriterionResult] = []
    frames = link_frames(ir)
    for joint in (j for j in ir.joints if j.kind == "fixed"):
        parent_mp, child_mp = mass_props[joint.parent], mass_props[joint.child]
        p_lo, p_hi = _world_bbox(link_geometry_transform(ir, joint.parent, frames), parent_mp)
        c_lo, c_hi = _world_bbox(link_geometry_transform(ir, joint.child, frames), child_mp)

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
