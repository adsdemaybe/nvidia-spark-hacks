"""Geometry generator registry — the only place topology-specific CAD logic
lives (§2: "a registry, never a hierarchy"). Adding a robot type means adding
generators, never touching the IR schema or the harness.

Units: the IR and everything outside this module speak SI (meters, kg).
build123d's native unit is millimeters — the mm/m conversion is contained
entirely inside this module and never leaks out.
"""

from __future__ import annotations

import shutil

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from build123d import Part, Shape

from engine.catalogue import MaterialSpec, resolve as resolve_catalogue
from engine.ir import CatalogueParam, GeometrySpec, Quantity, Vec3
from engine.mass_properties import InertiaTensor, MassProperties

_M_TO_MM = 1000.0
_MM3_TO_M3 = 1e-9
_MM_TO_M = 1e-3


@dataclass(frozen=True)
class CollisionShape:
    """One convex primitive approximating a link for contact simulation.

    Primitives, never the B-rep itself, and never its convex hull. A hull over
    a hollow part fills the hollow — the prototype shipped a tube whose hull was
    a solid cylinder, so a wheel that should have had a bore collided as though
    it were solid, and nothing in the report said so. A link may carry several
    of these; URDF and MJCF both accept multiple collision elements per body,
    and two boxes describe an L-bracket honestly where one box does not.

    `kind="box"`: `size` is (x, y, z) full extents, metres.
    `kind="cylinder"`: `size` is (radius, length), axis along the shape's local Z.
    `origin` is the primitive's centre in the link's geometry frame, metres.
    """

    kind: str
    size: tuple[float, ...]
    origin: Vec3


@dataclass
class GeometryResult:
    part: Part
    mass_properties: MassProperties
    # Contact geometry for tier 2+. Empty means "this generator has not declared
    # one", which the exporter reports rather than silently substituting a
    # bounding box — a wrong collider is worse than a missing one.
    collision: tuple[CollisionShape, ...] = ()


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


def _require_str(params: dict, key: str) -> str:
    """A string-valued param. Distinct from `_require` because asset paths and
    catalogue keys are identifiers, not measurements — attaching a Provenance to
    a filename would be provenance theatre."""
    if key not in params:
        raise KeyError(f"geometry param {key!r} is required, got: {sorted(params)}")
    value = params[key]
    if isinstance(value, CatalogueParam):
        return value.value
    if isinstance(value, Quantity):
        raise TypeError(f"geometry param {key!r} must be a string or CatalogueParam, not a Quantity")
    return str(value)


def _require(params: dict, key: str) -> Quantity:
    if key not in params:
        raise KeyError(f"geometry param {key!r} is required, got: {sorted(params)}")
    v = params[key]
    if not isinstance(v, Quantity):
        raise TypeError(f"geometry param {key!r} must be a Quantity, got {type(v).__name__}")
    return v


def _inertia_from_shape(shape: Shape, density_kg_per_m3: float) -> InertiaTensor:
    """Inertia about the shape's own centre of mass, in kg*m^2.

    `GProp_GProps.MatrixOfInertia()` is referenced to the **centre of mass**,
    not to the origin. That is a trap the prototype hit and paid for: treating
    it as origin-referenced and then "correcting" it with a parallel-axis shift
    subtracts a term that was never added, and silently produces tensors with
    negative eigenvalues — which MuJoCo and PhysX both reject at load, far away
    from the line that caused it.

    Verified rather than assumed: a 100 mm cube translated 200 mm along X
    returns Iyy = V*(a^2+b^2)/12, not V*(a^2+b^2)/12 + V*200^2. The offset does
    not appear, so the reference point is the centre of mass.

    The volume properties are unweighted by density (OCC's `Mass()` on a volume
    prop is the volume), so a single scalar multiply converts the whole tensor.
    """
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, props)
    matrix = props.MatrixOfInertia()
    # OCC works in the modeller's units: mm, so the raw tensor is volume-mm^2
    # == mm^5. Density is kg/m^3, hence mm^3->m^3 on the volume and mm^2->m^2
    # on the radius-of-gyration term, applied as one factor.
    scale = density_kg_per_m3 * _MM3_TO_M3 * (_MM_TO_M**2)
    return InertiaTensor(
        ixx=matrix.Value(1, 1) * scale,
        iyy=matrix.Value(2, 2) * scale,
        izz=matrix.Value(3, 3) * scale,
        ixy=matrix.Value(1, 2) * scale,
        ixz=matrix.Value(1, 3) * scale,
        iyz=matrix.Value(2, 3) * scale,
    )


def _mass_properties_from_shape(shape: Shape, density_kg_per_m3: float) -> MassProperties:
    volume_m3 = shape.volume * _MM3_TO_M3
    com = shape.center()
    bb = shape.bounding_box()
    return MassProperties(
        mass=volume_m3 * density_kg_per_m3,
        volume=volume_m3,
        com=Vec3(x=com.X * _MM_TO_M, y=com.Y * _MM_TO_M, z=com.Z * _MM_TO_M),
        inertia=_inertia_from_shape(shape, density_kg_per_m3),
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
    # The outer cylinder only. The bore is deliberately not represented: contact
    # against the inside of a tube is not something any primitive can express,
    # and the outer surface is the one that touches the ground. The *inertia*
    # still comes from the hollow solid, which is the half that matters.
    collision = (
        CollisionShape(
            kind="cylinder",
            size=(outer_d / 2 * _MM_TO_M, length * _MM_TO_M),
            origin=Vec3(x=0.0, y=0.0, z=length / 2 * _MM_TO_M),
        ),
    )
    return GeometryResult(
        part=part,
        mass_properties=_mass_properties_from_shape(part, material.density.value),
        collision=collision,
    )


@register("plate")
def _plate(params: dict, material: MaterialSpec) -> GeometryResult:
    """A rectangular plate: length along local X, width along Y, thickness along Z,
    with one corner at the local origin (base at Z=0)."""
    from build123d import Align, Box

    length = _require(params, "length").value * _M_TO_MM
    width = _require(params, "width").value * _M_TO_MM
    thickness = _require(params, "thickness").value * _M_TO_MM

    part = Box(length, width, thickness, align=(Align.MIN, Align.CENTER, Align.MIN))
    collision = (
        CollisionShape(
            kind="box",
            size=(length * _MM_TO_M, width * _MM_TO_M, thickness * _MM_TO_M),
            origin=Vec3(x=length / 2 * _MM_TO_M, y=0.0, z=thickness / 2 * _MM_TO_M),
        ),
    )
    return GeometryResult(
        part=part,
        mass_properties=_mass_properties_from_shape(part, material.density.value),
        collision=collision,
    )


@lru_cache(maxsize=128)
def _step_solid(asset: str, sha256: str | None, density: float):
    """Import a STEP and measure it, once per (asset, density).

    Both halves are cached, not just the import: computing volume, centre of
    mass and the inertia tensor over a real B-rep costs more than reading the
    file, and `evaluate()` builds every link on every candidate. A coverage
    sweep over a nine-part arm re-measures the same nine solids hundreds of
    times otherwise.
    """
    from engine.assets import load_step

    part = load_step(asset, sha256=sha256)
    return part, _mass_properties_from_shape(part, density)


@register("step_part")
def _step_part(params: dict, material: MaterialSpec) -> GeometryResult:
    """A vendored STEP solid — a real published part, not an approximation of one.

    This is the generator that lets an open-source design (SO-101 and friends)
    into the IR without redrawing it. The imported shape is a genuine B-rep, so
    volume, centre of mass and the full inertia tensor come out of the same
    OpenCascade path as every parametric part; nothing here is a mesh, and no
    inertia is estimated from a triangle soup.

    Mass is still density * volume. That is correct for a 3D-printed part and
    wrong for anything with embedded metal, which is why purchased components
    use the `component` generator below instead of being folded in here.

    Collision must be asked for explicitly. A STEP solid carries no primitive,
    and silently substituting its bounding box would put a solid brick where a
    slender arm is — so `collision="bbox"` is opt-in, visible in the IR, and
    still coarse. Without it the part has no collider and `to_urdf` refuses to
    export contact geometry for it rather than inventing some.
    """
    asset = _require_str(params, "asset")
    sha256 = params.get("sha256")
    part, mass_properties = _step_solid(
        asset, str(sha256) if sha256 else None, material.density.value
    )

    collision: tuple[CollisionShape, ...] = ()
    if str(params.get("collision", "none")) == "bbox":
        lo, hi = mass_properties.bbox_min, mass_properties.bbox_max
        collision = (
            CollisionShape(
                kind="box",
                size=(hi.x - lo.x, hi.y - lo.y, hi.z - lo.z),
                origin=Vec3(x=(lo.x + hi.x) / 2, y=(lo.y + hi.y) / 2, z=(lo.z + hi.z) / 2),
            ),
        )
    return GeometryResult(part=part, mass_properties=mass_properties, collision=collision)


@register("freeform")
def _freeform(params: dict, material: MaterialSpec) -> GeometryResult:
    """A solid described in build123d code rather than chosen from this registry.

    The generator that makes the platform text-to-CAD. Every other entry here is a shape
    somebody parameterised in advance, so a part nobody had anticipated could not be
    expressed at all — the design agent's vocabulary was five nouns. This one takes the
    code itself.

    What comes back is not special in any way, and that is deliberate: a genuine B-rep,
    measured through the same OpenCascade path as `tube` or `step_part`, so inertia, the
    URDF export and the co-sim gate cannot tell it apart and need no cases for it.

    The code runs in a subprocess and returns as STEP — see `freeform.py` for why. Mass is
    density x volume, same as every parametric part, which is right for a printed part and
    wrong for anything with embedded metal; those still belong in `component`.

    Collision is opt-in exactly as it is for `step_part`. A freeform solid carries no
    primitive, and substituting its bounding box unasked would put a brick where a bracket
    is.
    """
    from engine.geometry.freeform import FreeformError, run_to_step

    code = params.get("code")
    if code is None:
        raise ValueError("freeform requires a `code` param containing build123d source")
    # `code` arrives as a raw string, not a Quantity: it is not a measured value, and
    # wrapping it in one to satisfy the "never a bare number" rule would be a category
    # error. The rule is about numbers.
    source = code.value if hasattr(code, "value") else str(code)

    # A string, because IR params are CatalogueParam | Quantity | str and a timeout is
    # none of the first two — it is a harness knob, not a property of the design.
    step = run_to_step(source, timeout_s=int(str(_plain(params, "timeout_s", "40"))))
    try:
        from engine.assets import load_step_path

        part = load_step_path(step)
        try:
            mass_properties = _mass_properties_from_shape(part, material.density.value)
        except Exception as exc:
            # A surface, a wire or an empty boolean exports as valid STEP and then fails
            # the positive-mass validator several layers down, where the message is about
            # pydantic rather than about geometry. Re-raise it as something the model that
            # wrote the code can act on.
            raise FreeformError(
                "the code produced no solid — a sketch, a surface, or a boolean that "
                "removed everything. Extrude or revolve a sketch before assigning `part`. "
                f"({type(exc).__name__})"
            ) from exc
    finally:
        shutil.rmtree(step.parent, ignore_errors=True)

    collision: tuple[CollisionShape, ...] = ()
    if str(_plain(params, "collision", "none")) == "bbox":
        lo, hi = mass_properties.bbox_min, mass_properties.bbox_max
        collision = (
            CollisionShape(
                kind="box",
                size=(hi.x - lo.x, hi.y - lo.y, hi.z - lo.z),
                origin=Vec3(x=(lo.x + hi.x) / 2, y=(lo.y + hi.y) / 2, z=(lo.z + hi.z) / 2),
            ),
        )
    return GeometryResult(part=part, mass_properties=mass_properties, collision=collision)


def _plain(params: dict, key: str, default):
    """A param that is genuinely not a measurement — a flag, a name, a timeout."""
    v = params.get(key, default)
    return v.value if hasattr(v, "value") else v


@register("component")
def _component(params: dict, material: MaterialSpec) -> GeometryResult:
    """A purchased part, massed from its datasheet rather than from its volume.

    A servo is steel, copper, magnet and plastic in a case. Density times
    bounding volume is not its mass and no single material makes it so — the
    catalogue's figure is the measured fact, and this is the only generator
    allowed to take mass from somewhere other than geometry. `material` is
    ignored here for exactly that reason.

    The body dimensions are mandatory, and not cosmetically. A dimensionless
    point mass has a rank-deficient inertia tensor, and combining several of
    them yields a tensor that violates A + B >= C — physically impossible, and
    rejected outright by MuJoCo and PhysX. The prototype hit this and documented
    it; every component therefore contributes a real solid-box tensor.
    """
    from build123d import Align, Box

    from engine.catalogue import resolve as resolve_catalogue

    part_param = params.get("part")
    if not isinstance(part_param, CatalogueParam):
        raise TypeError("component geometry requires a `part` CatalogueParam naming the component")
    spec = resolve_catalogue(part_param.catalogue, part_param.value)

    size = getattr(spec, "body_size", None)
    if size is None:
        raise ValueError(
            f"catalogue part {part_param.value!r} has no body_size, so its inertia would be "
            "rank-deficient. Add the datasheet dimensions before using it as a component."
        )
    mass = spec.mass.value
    length, width, height = size.length.value, size.width.value, size.height.value

    part = Box(
        length * _M_TO_MM, width * _M_TO_MM, height * _M_TO_MM,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    volume = length * width * height
    inertia = InertiaTensor(
        ixx=mass / 12.0 * (width**2 + height**2),
        iyy=mass / 12.0 * (length**2 + height**2),
        izz=mass / 12.0 * (length**2 + width**2),
    )
    mass_properties = MassProperties(
        mass=mass,
        volume=volume,
        com=Vec3(x=0.0, y=0.0, z=height / 2),
        inertia=inertia,
        bbox_min=Vec3(x=-length / 2, y=-width / 2, z=0.0),
        bbox_max=Vec3(x=length / 2, y=width / 2, z=height),
    )
    collision = (
        CollisionShape(
            kind="box",
            size=(length, width, height),
            origin=Vec3(x=0.0, y=0.0, z=height / 2),
        ),
    )
    return GeometryResult(part=part, mass_properties=mass_properties, collision=collision)


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

    # The vertical arm is built `arm_b - thickness` tall on top of the horizontal
    # one. Without this the modeller is asked for a zero- or negative-height box
    # and raises a bare `Standard_DomainError` with no message — an OCCT internal
    # leaking to an agent that only needs to be told which parameter is wrong.
    if arm_b <= thickness:
        raise ValueError(
            f"bracket arm_b_length ({arm_b}mm) must exceed thickness ({thickness}mm): "
            "the vertical arm stands on top of the horizontal one"
        )
    if arm_a <= 0 or width <= 0:
        raise ValueError(f"bracket arm_a_length ({arm_a}mm) and width ({width}mm) must be positive")

    horizontal = Box(arm_a, width, thickness, align=(Align.MIN, Align.CENTER, Align.MIN))
    vertical = Pos(0, 0, thickness) * Box(
        thickness, width, arm_b - thickness, align=(Align.MIN, Align.CENTER, Align.MIN)
    )
    part = horizontal + vertical
    # Two boxes, one per arm — the same decomposition the solid is built from.
    # A single box over the whole L would fill the inside corner with material
    # that isn't there, which is exactly the volume a motor is mounted in.
    collision = (
        CollisionShape(
            kind="box",
            size=(arm_a * _MM_TO_M, width * _MM_TO_M, thickness * _MM_TO_M),
            origin=Vec3(x=arm_a / 2 * _MM_TO_M, y=0.0, z=thickness / 2 * _MM_TO_M),
        ),
        CollisionShape(
            kind="box",
            size=(thickness * _MM_TO_M, width * _MM_TO_M, (arm_b - thickness) * _MM_TO_M),
            origin=Vec3(
                x=thickness / 2 * _MM_TO_M,
                y=0.0,
                z=(thickness + (arm_b - thickness) / 2) * _MM_TO_M,
            ),
        ),
    )
    return GeometryResult(
        part=part,
        mass_properties=_mass_properties_from_shape(part, material.density.value),
        collision=collision,
    )
