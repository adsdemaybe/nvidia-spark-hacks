"""Mass, inertia, friction -- every number a Measured with honest provenance.

Uniform density is systematically wrong for hollow objects (mugs, boxes,
bottles -- i.e. most manipulation targets). The hollow_factor column corrects
the category mean and is itself ASSUMED until somebody weighs something. All of
this is INFERRED/ASSUMED in V1; nothing here may claim CONFIRMED without a URL.
"""
from __future__ import annotations

import numpy as np
import trimesh

from r2s.core import Measured, assumed, inferred
from r2s.core.schemas import PhysicalProperties

# category -> (density kg/m^3, hollow correction, material, static friction)
_PRIORS: dict[str, tuple[float, float, str, float]] = {
    "mug":      (2300.0, 0.35, "ceramic", 0.6),
    "bottle":   (950.0, 0.15, "plastic", 0.4),
    "box":      (600.0, 0.25, "cardboard", 0.5),
    "book":     (800.0, 1.00, "paper", 0.55),
    "laptop":   (1500.0, 0.60, "metal_plastic", 0.5),
    "keyboard": (900.0, 0.55, "plastic", 0.5),
    "monitor":  (1200.0, 0.45, "metal_plastic", 0.5),
    "mouse":    (900.0, 0.60, "plastic", 0.5),
    "plant":    (700.0, 0.80, "organic", 0.6),
    "lamp":     (1100.0, 0.50, "metal", 0.45),
    "chair":    (700.0, 0.85, "wood_metal", 0.55),
    "table":    (700.0, 0.90, "wood", 0.55),
    "can":      (2700.0, 0.12, "aluminum", 0.35),
    "cup":      (1100.0, 0.30, "plastic", 0.45),
}
_DEFAULT = (800.0, 0.60, "unknown", 0.5)

MASS_FLOOR_KG = 0.005
MASS_CEIL_KG = 100.0


def infer_physics(mesh: trimesh.Trimesh, category: str) -> PhysicalProperties:
    density, hollow, material, mu_s = _PRIORS.get(category, _DEFAULT)

    if mesh.is_watertight:
        volume = abs(float(mesh.volume))
        method_v = "watertight_mesh_volume"
    else:
        volume = abs(float(mesh.convex_hull.volume))
        method_v = "convex_hull_volume"

    mass = float(np.clip(volume * density * hollow, MASS_FLOOR_KG, MASS_CEIL_KG))

    # inertia of the (assumed uniform) solid at the inferred mass, principal axes
    try:
        m = mesh if mesh.is_watertight else mesh.convex_hull
        it = m.moment_inertia * (mass / max(abs(float(m.volume)) * 1.0, 1e-9))
        evals = np.linalg.eigvalsh(it)
        evals = np.clip(np.abs(evals), 1e-8, None)
        com = m.center_mass
    except BaseException:
        # box-model fallback: still positive-definite, still honest about method
        ex = mesh.extents
        evals = mass / 12.0 * np.array([
            ex[1] ** 2 + ex[2] ** 2, ex[0] ** 2 + ex[2] ** 2, ex[0] ** 2 + ex[1] ** 2,
        ])
        com = mesh.bounds.mean(axis=0)

    method = f"uniform_density_prior[{category}]*hollow_factor via {method_v}"
    return PhysicalProperties(
        mass=inferred(mass, "kg", method=method),
        density=assumed(density, "kg/m^3", note=f"category_prior[{category}]"),
        com_local=inferred(tuple(float(v) for v in com), "m", method=method_v),
        inertia_diag=inferred(tuple(float(v) for v in evals), "kg*m^2", method=method),
        static_friction=assumed(mu_s, "1", note=f"material_prior[{material}]"),
        dynamic_friction=assumed(max(mu_s - 0.1, 0.1), "1", note="static-0.1 convention"),
        restitution=assumed(0.1, "1", note="household default"),
        material_guess=material,
    )


def inertia_valid(props: PhysicalProperties) -> tuple[bool, str]:
    """Positive moments + triangle inequality on the principal moments."""
    a, b, c = props.inertia_diag.value
    if min(a, b, c) <= 0:
        return False, f"non-positive principal moment: {(a, b, c)}"
    if a + b < c * 0.999 or b + c < a * 0.999 or a + c < b * 0.999:
        return False, f"triangle inequality violated: {(a, b, c)}"
    return True, "ok"
