"""Geometry generator registry — the only place topology-specific CAD logic
lives (§2: "a registry, never a hierarchy"). Adding a robot type means adding
generators, never touching the IR schema or the harness.

Units: the IR and everything outside this module speak SI (meters, kg).
build123d's native unit is millimeters — the mm/m conversion is contained
entirely inside this module and never leaks out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from build123d import Part, Shape

from engine.catalogue import MaterialSpec, resolve as resolve_catalogue
from engine.ir import CatalogueParam, GeometrySpec, Quantity, Vec3
from engine.mass_properties import MassProperties

_M_TO_MM = 1000.0
_MM3_TO_M3 = 1e-9
_MM_TO_M = 1e-3


@dataclass
class GeometryResult:
    part: Part
    mass_properties: MassProperties


GeneratorFn = Callable[[dict[str, "CatalogueParam | Quantity"], MaterialSpec], GeometryResult]

_REGISTRY: dict[str, GeneratorFn] = {}


def register(name: str) -> Callable[[GeneratorFn], GeneratorFn]:
    def decorator(fn: GeneratorFn) -> GeneratorFn:
        if name in _REGISTRY:
            raise ValueError(f"geometry generator {name!r} already registered")
        _REGISTRY[name] = fn
        return fn

    return decorator


def generators() -> list[str]:
    return sorted(_REGISTRY)


def _require(params: dict, key: str) -> Quantity:
    if key not in params:
        raise KeyError(f"geometry param {key!r} is required, got: {sorted(params)}")
    v = params[key]
    if not isinstance(v, Quantity):
        raise TypeError(f"geometry param {key!r} must be a Quantity, got {type(v).__name__}")
    return v


def _mass_properties_from_shape(shape: Shape, density_kg_per_m3: float) -> MassProperties:
    volume_m3 = shape.volume * _MM3_TO_M3
    com = shape.center()
    bb = shape.bounding_box()
    return MassProperties(
        mass=volume_m3 * density_kg_per_m3,
        volume=volume_m3,
        com=Vec3(x=com.X * _MM_TO_M, y=com.Y * _MM_TO_M, z=com.Z * _MM_TO_M),
        bbox_min=Vec3(x=bb.min.X * _MM_TO_M, y=bb.min.Y * _MM_TO_M, z=bb.min.Z * _MM_TO_M),
        bbox_max=Vec3(x=bb.max.X * _MM_TO_M, y=bb.max.Y * _MM_TO_M, z=bb.max.Z * _MM_TO_M),
    )


def build(spec: GeometrySpec) -> GeometryResult:
    if spec.generator not in _REGISTRY:
        raise KeyError(f"no geometry generator {spec.generator!r}; known: {generators()}")
    material: MaterialSpec = resolve_catalogue("materials", spec.material.value)
    return _REGISTRY[spec.generator](spec.params, material)


# --- built-in generators -----------------------------------------------


@register("tube")
def _tube(params: dict, material: MaterialSpec) -> GeometryResult:
    """A hollow cylinder, axis along local Z, base at local origin."""
    from build123d import Align, Cylinder

    outer_d = _require(params, "outer_diameter").value * _M_TO_MM
    inner_d = _require(params, "inner_diameter").value * _M_TO_MM
    length = _require(params, "length").value * _M_TO_MM
    if inner_d >= outer_d:
        raise ValueError(f"tube inner_diameter ({inner_d}mm) must be < outer_diameter ({outer_d}mm)")

    align = (Align.CENTER, Align.CENTER, Align.MIN)
    outer = Cylinder(radius=outer_d / 2, height=length, align=align)
    inner = Cylinder(radius=inner_d / 2, height=length, align=align)
    part = outer - inner
    return GeometryResult(part=part, mass_properties=_mass_properties_from_shape(part, material.density.value))


@register("plate")
def _plate(params: dict, material: MaterialSpec) -> GeometryResult:
    """A rectangular plate: length along local X, width along Y, thickness along Z,
    with one corner at the local origin (base at Z=0)."""
    from build123d import Align, Box

    length = _require(params, "length").value * _M_TO_MM
    width = _require(params, "width").value * _M_TO_MM
    thickness = _require(params, "thickness").value * _M_TO_MM

    part = Box(length, width, thickness, align=(Align.MIN, Align.CENTER, Align.MIN))
    return GeometryResult(part=part, mass_properties=_mass_properties_from_shape(part, material.density.value))


@register("bracket")
def _bracket(params: dict, material: MaterialSpec) -> GeometryResult:
    """An L-shaped bracket: a horizontal arm along local X and a vertical arm
    along local Z, sharing `thickness` and `width`, meeting at the origin corner.
    """
    from build123d import Align, Box, Pos

    arm_a = _require(params, "arm_a_length").value * _M_TO_MM  # horizontal (X)
    arm_b = _require(params, "arm_b_length").value * _M_TO_MM  # vertical (Z)
    thickness = _require(params, "thickness").value * _M_TO_MM
    width = _require(params, "width").value * _M_TO_MM

    horizontal = Box(arm_a, width, thickness, align=(Align.MIN, Align.CENTER, Align.MIN))
    vertical = Pos(0, 0, thickness) * Box(
        thickness, width, arm_b - thickness, align=(Align.MIN, Align.CENTER, Align.MIN)
    )
    part = horizontal + vertical
    return GeometryResult(part=part, mass_properties=_mass_properties_from_shape(part, material.density.value))
