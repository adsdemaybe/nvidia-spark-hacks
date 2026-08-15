"""Scene -> USD. Authored via pxr (usd-core) when available; the Isaac target.

Layout mirrors the NuRec composite design:
  /World
    /Shell         -- NuRec reference (matte) + proxy collision, when present
    /obj_NNN       -- one Xform per placement: visual mesh + PhysX APIs
    /Robot         -- base frame marker for the SO-101 (articulation lands in V2)

Physics via UsdPhysics applied APIs so Isaac Sim picks them up natively.
Deterministic authoring: fixed prim ordering, fixed float formatting -- .usda
text is golden-file testable.
"""
from __future__ import annotations

from pathlib import Path

from r2s.core.schemas import AssetPayload, Placement, ScenePayload, ShellPayload


def write_scene_usda(
    scene: ScenePayload,
    shell: ShellPayload,
    assets: dict[str, AssetPayload],
    fetch_visual,  # (asset_id) -> Path to a GLB/OBJ on disk (unused when pxr absent)
    fetch_collision,  # (asset_id, hull_index) -> Path to an OBJ on disk
    out_path: Path,
) -> Path | None:
    try:
        import trimesh
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics
    except ImportError:
        return None  # caller records the degradation; MJCF remains authoritative

    out_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(out_path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    # ---- shell ----------------------------------------------------------
    shell_x = UsdGeom.Xform.Define(stage, "/World/Shell")
    shell_x.GetPrim().SetMetadata("comment",
                                  "NuRec splat reference lands here on the Spark "
                                  "(matte + Proxy binding); floor plane stands in meanwhile")
    floor = UsdGeom.Cube.Define(stage, "/World/Shell/FloorProxy")
    floor.CreateSizeAttr(1.0)
    fx = UsdGeom.XformCommonAPI(floor)
    fx.SetScale(Gf.Vec3f(20.0, 20.0, 0.05))
    fx.SetTranslate(Gf.Vec3d(0.0, 0.0, -0.025))
    UsdPhysics.CollisionAPI.Apply(floor.GetPrim())

    # ---- placements -----------------------------------------------------
    for pl in sorted(scene.placements, key=lambda p: p.prim_path):
        a = assets[pl.asset_id]
        pose = pl.settled_pose or pl.pose
        x = UsdGeom.Xform.Define(stage, pl.prim_path)
        api = UsdGeom.XformCommonAPI(x)
        api.SetTranslate(Gf.Vec3d(*[round(v, 6) for v in pose.position]))
        w, qx, qy, qz = pose.quaternion
        x.GetPrim().CreateAttribute("xformOp:orient", Sdf.ValueTypeNames.Quatd)
        # orient via the raw op to keep wxyz explicit and deterministic
        x.ClearXformOpOrder()
        top = x.AddTranslateOp()
        top.Set(Gf.Vec3d(*[round(v, 6) for v in pose.position]))
        rop = x.AddOrientOp(UsdGeom.XformOp.PrecisionDouble)
        rop.Set(Gf.Quatd(round(w, 6), Gf.Vec3d(round(qx, 6), round(qy, 6), round(qz, 6))))

        prim = x.GetPrim()
        prim.SetMetadata("comment", f"asset={a.asset_id} provenance={a.provenance}")
        prim.CreateAttribute("r2s:assetId", Sdf.ValueTypeNames.String).Set(a.asset_id)
        prim.CreateAttribute("r2s:provenance", Sdf.ValueTypeNames.String).Set(str(a.provenance))
        prim.CreateAttribute("r2s:role", Sdf.ValueTypeNames.String).Set(pl.role)

        if pl.role != "fixture":
            UsdPhysics.RigidBodyAPI.Apply(prim)
            mass_api = UsdPhysics.MassAPI.Apply(prim)
            mass_api.CreateMassAttr(float(a.physics.mass.value))
            mass_api.CreateCenterOfMassAttr(Gf.Vec3f(*a.physics.com_local.value))
            mass_api.CreateDiagonalInertiaAttr(Gf.Vec3f(*a.physics.inertia_diag.value))

        # collision hulls as explicit convex meshes
        for hi in range(a.collision.n_hulls):
            hull_path = fetch_collision(a.asset_id, hi)
            hull = trimesh.load(hull_path, force="mesh")
            mesh = UsdGeom.Mesh.Define(stage, f"{pl.prim_path}/collision_{hi:02d}")
            mesh.CreatePointsAttr([Gf.Vec3f(*[float(c) for c in v]) for v in hull.vertices])
            mesh.CreateFaceVertexCountsAttr([3] * len(hull.faces))
            mesh.CreateFaceVertexIndicesAttr([int(i) for f in hull.faces for i in f])
            mesh.CreatePurposeAttr(UsdGeom.Tokens.guide)
            UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
            mc = UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim())
            mc.CreateApproximationAttr(UsdPhysics.Tokens.convexHull)

    # ---- robot base marker ---------------------------------------------
    rb = UsdGeom.Xform.Define(stage, "/World/Robot")
    UsdGeom.XformCommonAPI(rb).SetTranslate(
        Gf.Vec3d(*[round(v, 6) for v in scene.robot.base_pose.position])
    )
    rb.GetPrim().CreateAttribute("r2s:robot", Sdf.ValueTypeNames.String).Set(scene.robot.name)

    stage.GetRootLayer().Save()
    return out_path
