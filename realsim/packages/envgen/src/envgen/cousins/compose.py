"""Scene composition: turn constraints into sampling domains, not rejections.

The organizing idea (design plan §0): a gate that only rejects gives a low
acceptance rate and a long night; the same predicate used to constrain the
sampling domain gives near-100% acceptance. So:

  * reachability is a POLYGON intersected into the placement region before any
    pose is drawn -- placements are reachable by construction;
  * support polygons arrive pre-eroded (edge margin) from assetize/shell;
  * XY sampling is area-weighted triangulation, not bbox rejection (a thin
    L-shaped desk region would loop near-forever);
  * placement order follows the support DAG (tables before mugs).

Fast validity here is 2D: footprint-overlap via shapely + AABB height bands.
The full 3D contact truth comes from the MuJoCo settle right after -- cheap 2D
first, physics second, exactly the cheap->expensive ordering.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import shapely.geometry as sg

from r2s.core import Pose
from r2s.core.schemas import AssetPayload, Placement, RobotSpec


@dataclass
class PlacedInstance:
    instance_id: str
    asset: AssetPayload
    pose: Pose
    footprint: sg.Polygon
    top_height: float
    support_surface_id: str
    role: str = "distractor"


@dataclass
class Workspace:
    """The composition state for one scene draw."""
    room: sg.Polygon
    robot: RobotSpec
    placed: list[PlacedInstance] = field(default_factory=list)
    surfaces: dict[str, tuple[sg.Polygon, float, str | None]] = field(default_factory=dict)
    # surface_id -> (polygon(world XY), height, owner instance id)

    def occupied_on(self, surface_id: str) -> sg.MultiPolygon | sg.Polygon:
        polys = [p.footprint for p in self.placed if p.support_surface_id == surface_id]
        return sg.MultiPolygon(polys) if polys else sg.Polygon()


def reach_polygon(robot: RobotSpec, surface_height: float) -> sg.Polygon:
    """The annulus the SO-101 can reach at a given height, in world XY.

    5-DOF arm: we constrain position + approach, so the reachable set at a
    height is well-approximated by an annulus of horizontal radius
    sqrt(reach^2 - dz^2). Conservative by construction; the per-task IK check
    still runs, it just almost always passes because sampling stayed inside.
    """
    base = robot.base_pose.position
    dz = surface_height - base[2]
    lo_h, hi_h = robot.height_band_m
    if not (lo_h <= dz <= hi_h):
        return sg.Polygon()
    r_out_sq = robot.reach_max_m**2 - dz**2
    if r_out_sq <= 0:
        return sg.Polygon()
    r_out = float(np.sqrt(r_out_sq))
    r_in = robot.reach_min_m
    c = sg.Point(base[0], base[1])
    ring = c.buffer(r_out, quad_segs=32)
    if r_in > 0:
        ring = ring.difference(c.buffer(r_in, quad_segs=16))
    return ring


def footprint_at(asset: AssetPayload, x: float, y: float, yaw: float) -> sg.Polygon:
    """Asset AABB footprint, rotated and placed. AABB (not the mesh outline) is
    the deliberate conservative choice for the 2D broadphase."""
    hx, hy = asset.bbox_m[0] / 2, asset.bbox_m[1] / 2
    box = sg.Polygon([(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)])
    from shapely import affinity

    box = affinity.rotate(box, np.degrees(yaw), origin=(0, 0))
    return affinity.translate(box, x, y)


def sample_point_in_polygon(poly: sg.Polygon, rng: np.random.Generator) -> tuple[float, float] | None:
    """Area-weighted triangle sampling. Handles L-shapes and slivers that
    defeat bbox rejection."""
    if poly.is_empty or poly.area < 1e-8:
        return None
    try:
        import shapely.ops

        tris = shapely.ops.triangulate(poly)
    except Exception:
        tris = []
    tris = [t for t in tris if t.within(poly.buffer(1e-9)) or poly.contains(t.centroid)]
    if not tris:  # degenerate: fall back to centroid
        c = poly.representative_point()
        return float(c.x), float(c.y)
    areas = np.array([t.area for t in tris])
    t = tris[int(rng.choice(len(tris), p=areas / areas.sum()))]
    (x0, y0), (x1, y1), (x2, y2) = list(t.exterior.coords)[:3]
    a, b = rng.random(), rng.random()
    if a + b > 1:
        a, b = 1 - a, 1 - b
    return (x0 + a * (x1 - x0) + b * (x2 - x0), y0 + a * (y1 - y0) + b * (y2 - y0))


def sample_placement(
    ws: Workspace,
    asset: AssetPayload,
    surface_id: str,
    rng: np.random.Generator,
    *,
    require_reachable: bool = False,
    near_xy: tuple[float, float] | None = None,
    near_radius: float = 0.4,
    max_tries: int = 64,
    clearance: float = 0.005,
) -> PlacedInstance | None:
    """Draw a valid pose on a surface, or None after max_tries."""
    surf_poly, surf_h, owner = ws.surfaces[surface_id]
    region = surf_poly

    if require_reachable:
        region = region.intersection(reach_polygon(ws.robot, surf_h))
    if near_xy is not None:
        region = region.intersection(sg.Point(near_xy).buffer(near_radius, quad_segs=16))
    if region.is_empty:
        return None  # a NAMED conflict: the caller logs which constraint emptied it
    if hasattr(region, "geoms"):
        region = max(region.geoms, key=lambda g: g.area)

    occupied = ws.occupied_on(surface_id)
    stable = asset.stable_poses or [Pose()]
    probs = np.asarray(asset.stable_pose_probs or [1.0], dtype=float)
    probs = probs / probs.sum()

    for _ in range(max_tries):
        pt = sample_point_in_polygon(region, rng)
        if pt is None:
            return None
        yaw = float(rng.uniform(0, 2 * np.pi))
        fp = footprint_at(asset, pt[0], pt[1], yaw)
        if not region.buffer(1e-6).contains(fp.centroid):
            continue
        if not surf_poly.contains(fp):  # whole footprint on the surface
            continue
        if occupied.intersects(fp.buffer(clearance)):
            continue

        k = int(rng.choice(len(stable), p=probs))
        base_q = np.asarray(stable[k].quaternion)
        yaw_q = np.array([np.cos(yaw / 2), 0.0, 0.0, np.sin(yaw / 2)])
        q = _quat_mul(yaw_q, base_q)
        pose = Pose(position=(pt[0], pt[1], surf_h + 1e-3),
                    quaternion=tuple(float(v) for v in q))
        return PlacedInstance(
            instance_id="",  # caller assigns
            asset=asset,
            pose=pose,
            footprint=fp,
            top_height=surf_h + asset.bbox_m[2],
            support_surface_id=surface_id,
        )
    return None


def register_asset_surfaces(ws: Workspace, inst: PlacedInstance) -> None:
    """A placed asset contributes its own support faces (table tops) to the
    workspace, transformed into world XY."""
    from shapely import affinity

    yaw = 2 * np.arctan2(inst.pose.quaternion[3], inst.pose.quaternion[0])
    for f in inst.asset.support_faces:
        poly = sg.Polygon(f.polygon)
        poly = affinity.rotate(poly, np.degrees(yaw), origin=(0, 0))
        poly = affinity.translate(poly, inst.pose.position[0], inst.pose.position[1])
        sid = f"{inst.instance_id}/{f.face_id}"
        ws.surfaces[sid] = (poly, inst.pose.position[2] + f.height_m, inst.instance_id)


def _quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ])


def to_placement(inst: PlacedInstance, prim_index: int) -> Placement:
    return Placement(
        instance_id=inst.instance_id,
        asset_id=inst.asset.asset_id,
        asset_provenance=inst.asset.provenance,
        source_object_id=inst.asset.source_object_id,
        category=inst.asset.category,
        pose=inst.pose,
        support_surface_id=inst.support_surface_id,
        role=inst.role,  # type: ignore[arg-type]
        prim_path=f"/World/obj_{prim_index:03d}",
    )
