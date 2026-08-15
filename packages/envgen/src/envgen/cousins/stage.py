"""Stage 7: cousins. shell + asset bundles -> N validated cousin scenes.

Stratification (the 8-scene split; N is config, nothing hardcodes 8):

  C0    TWIN     scanned assets, scanned layout          train  (eval anchor, F5 overlay)
  C1-3  COUSIN   identity swapped, layout ~ scanned      train  (axis 1, highest value)
  C4-5  COUSIN   identity + layout varied, +/-1 object   train  (axis 2)
  C6    COUSIN   resampled identity+layout, task B       HELDOUT (axis 3)
  C7    COUSIN   ASSET-DISJOINT from C0-C6, task C       HELDOUT (full shift)

Retry budgets, explicit and loud (never ship N-1 scenes silently):
  L1: 64 placement tries per object (inside compose.sample_placement)
  L2: 8 scene re-seeds per asset draw
  L3: 5 asset re-draws per stratum -> GenerationFailure naming the worst gate

Determinism: every draw comes from ctx.rng(scope...); the settled poses are
BAKED into the artifact because MuJoCo is not bitwise stable across machines --
the artifact is deterministic even though the process is not.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import shapely.geometry as sg
from pydantic import BaseModel

from r2s.core import (
    ArtifactRef,
    Degradation,
    DegradationLevel,
    Pose,
    Proposer,
)
from r2s.core.runctx import RunContext, StageSpec
from r2s.core.schemas import (
    AssetBundlePayload,
    AssetPayload,
    Placement,
    RobotSpec,
    ScenePayload,
    SegmentationPayload,
    SettleReport,
    ShellPayload,
    VariationRecord,
)

from .compose import (
    PlacedInstance,
    Workspace,
    reach_polygon,
    register_asset_surfaces,
    sample_placement,
    to_placement,
)
from .diversity import min_distance_to_set
from .mjcf import build_mjcf, settle

FIXTURE_CATEGORIES = {"table", "desk", "chair", "shelf", "lamp", "monitor"}
TASK_FAMILIES = {"A": "pick_place", "B": "place_near", "C": "push_to"}


class CousinsConfig(BaseModel):
    n_scenes: int = 8
    diversity_floor: float = 0.30
    settle_steps: int = 240
    settle_max_translation_m: float = 0.05
    settle_max_rotation_deg: float = 10.0
    settle_max_penetration_m: float = 0.002
    l2_budget: int = 8
    l3_budget: int = 5


class CousinSetPayload(BaseModel):
    """The stage-level artifact: refs to every scene + the rejection ledger."""

    schema_version: str = "1.0.0"
    scene_refs: dict[str, ArtifactRef] = {}
    scene_ids: list[str] = []
    heldout_ids: list[str] = []
    rejections_ref: ArtifactRef | None = None
    attempts: int = 0
    accepted: int = 0
    rejected: int = 0


SPEC = StageSpec(
    name="cousins",
    version="1",
    artifact_type="cousin_set",
    payload_model=CousinSetPayload,  # per-scene ScenePayload artifacts hang off this set
    relevant_packages=["r2s-core", "envgen", "mujoco", "shapely", "trimesh"],
)


class GenerationFailure(RuntimeError):
    pass


@dataclass
class Stratum:
    scene_id: str
    kind: str  # TWIN | COUSIN
    identity: str  # scanned | swap
    layout: str  # scanned | varied
    task_family: str
    split: str  # train | heldout
    disjoint: bool = False


def build_strata(n: int) -> list[Stratum]:
    """Scale the C0-C7 pattern to any n>=3: always one twin, always >=1 heldout,
    always one disjoint stratum last."""
    strata = [Stratum("c0", "TWIN", "scanned", "scanned", "A", "train")]
    n_identity = max(1, (n - 3) // 2)
    n_layout = max(1, n - 3 - n_identity)
    i = 1
    for _ in range(n_identity):
        strata.append(Stratum(f"c{i}", "COUSIN", "swap", "scanned", "A", "train")); i += 1
    for _ in range(n_layout):
        strata.append(Stratum(f"c{i}", "COUSIN", "swap", "varied", "A", "train")); i += 1
    strata.append(Stratum(f"c{i}", "COUSIN", "swap", "varied", "B", "heldout")); i += 1
    strata.append(Stratum(f"c{i}", "COUSIN", "swap", "varied", "C", "heldout", disjoint=True))
    return strata[:n] if len(strata) > n else strata


def run(inputs: dict, cfg: CousinsConfig, ctx: RunContext):
    shell_art = inputs["shell"]
    shell: ShellPayload = shell_art.payload
    scanned: AssetBundlePayload = inputs["assetize"].payload
    generated: AssetBundlePayload = inputs["generate"].payload
    seg: SegmentationPayload = inputs["segment"].payload
    scratch = ctx.scratch("cousins")
    degs: list[Degradation] = []

    all_assets: dict[str, AssetPayload] = {**scanned.assets, **generated.assets}
    orig_pose = {o.object_id: o for o in seg.objects}
    room_poly = sg.Polygon(shell.room_polygon_m)
    room_diag = float(np.sqrt(shell.room_area_m2)) * 1.5

    hull_cache: dict[tuple[str, int], object] = {}

    def fetch_collision(asset_id: str, hi: int):
        key = (asset_id, hi)
        if key not in hull_cache:
            p = scratch / "hulls" / f"{asset_id}_h{hi}.obj"
            p.parent.mkdir(exist_ok=True)
            ctx.store.fetch(all_assets[asset_id].collision_refs[hi], p)
            hull_cache[key] = p
        return hull_cache[key]

    strata = build_strata(cfg.n_scenes)
    accepted: list[ScenePayload] = []
    scene_refs: dict[str, ArtifactRef] = {}
    used_ids: set[str] = set()
    rejections: list[dict] = []
    attempts = 0

    for stratum in strata:
        placed_scene = None
        gate_fail_counts: dict[str, int] = {}
        for l3 in range(cfg.l3_budget):
            picks = _draw_assets(stratum, scanned, generated, used_ids, ctx, l3)
            if picks is None:
                gate_fail_counts["asset_pool_exhausted"] = gate_fail_counts.get(
                    "asset_pool_exhausted", 0) + 1
                continue
            for l2 in range(cfg.l2_budget):
                attempts += 1
                rng = ctx.rng("cousins", stratum.scene_id, l3, l2)
                try:
                    cand = _compose_scene(
                        stratum, picks, shell, orig_pose, room_poly, rng, ctx
                    )
                except _ComposeReject as cr:
                    rejections.append(_rej(stratum, l3, l2, "compose", str(cr)))
                    gate_fail_counts["compose"] = gate_fail_counts.get("compose", 0) + 1
                    continue

                # cheap gate first: diversity vs already-accepted
                d = min_distance_to_set(cand, accepted, room_diag=room_diag)
                if stratum.kind != "TWIN" and d < cfg.diversity_floor:
                    rejections.append(_rej(stratum, l3, l2, "diversity", f"D={d:.3f}"))
                    gate_fail_counts["diversity"] = gate_fail_counts.get("diversity", 0) + 1
                    continue

                # expensive gate last: physics settle
                mjcf_path = scratch / stratum.scene_id / f"try_{l3}_{l2}.xml"
                movable = [p.instance_id for p in cand.placements if p.role != "fixture"]
                build_mjcf(shell, cand.placements, all_assets, fetch_collision, mjcf_path)
                out = settle(mjcf_path, movable, steps=cfg.settle_steps)
                okay = (not out.exploded
                        and out.max_translation_m <= cfg.settle_max_translation_m
                        and out.max_rotation_deg <= cfg.settle_max_rotation_deg
                        and out.max_penetration_m <= cfg.settle_max_penetration_m)
                if not okay:
                    rejections.append(_rej(
                        stratum, l3, l2, "settle",
                        f"dT={out.max_translation_m:.3f}m dR={out.max_rotation_deg:.1f}deg "
                        f"pen={out.max_penetration_m*1000:.1f}mm exploded={out.exploded}",
                    ))
                    gate_fail_counts["settle"] = gate_fail_counts.get("settle", 0) + 1
                    continue

                # bake settled poses + final artifacts
                cand = _bake(cand, out)
                final_mjcf = scratch / stratum.scene_id / "scene.xml"
                build_mjcf(shell, cand.placements, all_assets, fetch_collision, final_mjcf)
                mjcf_ref = ctx.store.put_file(final_mjcf, "application/xml")
                usd_ref = _write_usd(cand, shell, all_assets, fetch_collision, scratch, degs, ctx)
                cand = cand.model_copy(update={
                    "mjcf_ref": mjcf_ref, "usd_ref": usd_ref,
                    "diversity_vs_siblings": {
                        s.scene_id: round(min_distance_to_set(cand, [s], room_diag=room_diag), 3)
                        for s in accepted
                    },
                })
                placed_scene = cand
                break
            if placed_scene is not None:
                break

        if placed_scene is None:
            worst = max(gate_fail_counts.items(), key=lambda kv: kv[1], default=("none", 0))
            raise GenerationFailure(
                f"stratum {stratum.scene_id} exhausted all budgets. worst gate: "
                f"{worst[0]} ({worst[1]} rejections). last rejections: "
                f"{json.dumps(rejections[-5:], indent=1)}"
            )

        if stratum.disjoint:
            leak = placed_scene.asset_ids() & used_ids
            if leak:  # mechanical assertion -- a leak silently invalidates the eval
                raise GenerationFailure(f"disjoint stratum {stratum.scene_id} leaked assets: {leak}")
        used_ids |= placed_scene.asset_ids()
        accepted.append(placed_scene)

        # per-scene artifact through the SAME header machinery as everything else
        from r2s.core.artifacts import ArtifactHeader, BaseArtifact, sha256_of

        h = ArtifactHeader(
            schema_version=placed_scene.schema_version, artifact_type="scene",
            artifact_id=sha256_of(placed_scene), run_id=ctx.run_id, stage="cousins",
            backend="composer", input_digest="see-set-artifact", seed=ctx.seed,
            degradation=shell_art.worst_degradation(),
            degradations=shell_art.header.degradations,
        )
        scene_refs[stratum.scene_id] = ctx.store.put_artifact(
            BaseArtifact(header=h, payload=placed_scene)
        )

    rej_bytes = ("\n".join(json.dumps(r) for r in rejections)).encode() or b"{}"
    payload = CousinSetPayload(
        scene_refs=scene_refs,
        scene_ids=[s.scene_id for s in accepted],
        heldout_ids=[s.scene_id for s in accepted if s.split == "heldout"],
        rejections_ref=ctx.store.put_bytes(rej_bytes, "application/x-ndjson"),
        attempts=attempts,
        accepted=len(accepted),
        rejected=len(rejections),
    )
    # accounting: no attempt may vanish
    assert payload.accepted + payload.rejected == payload.attempts, "attempt accounting broken"
    return payload, "composer", degs


# ---------------------------------------------------------------------------
class _ComposeReject(Exception):
    pass


def _draw_assets(stratum, scanned, generated, used_ids, ctx, l3):
    """Pick the asset set for one stratum. Swap draws WITHOUT replacement from
    the per-category top-K pools -- top-1 everywhere would silently collapse
    the identity axis into pose jitter."""
    rng = ctx.rng("draw", stratum.scene_id, l3)
    if stratum.identity == "scanned":
        return dict(scanned.assets)

    picks: dict[str, AssetPayload] = {}
    for sid, s_asset in scanned.assets.items():
        pool = [a for a in generated.assets.values() if a.category == s_asset.category]
        if stratum.disjoint:
            pool = [a for a in pool if a.asset_id not in used_ids]
        if not pool:
            if stratum.disjoint:
                return None  # pool exhausted; L3 will retry then fail loudly
            picks[sid] = s_asset  # identity fallback, recorded via provenance
            continue
        pick = pool[int(rng.integers(len(pool)))]
        picks[sid] = pick.model_copy(update={"source_object_id": sid})
    return picks


def _compose_scene(stratum, picks, shell, orig_pose, room_poly, rng, ctx) -> ScenePayload:
    ws = Workspace(room=room_poly, robot=RobotSpec())
    ws.surfaces["floor"] = (room_poly, 0.0, None)

    fixtures = {k: a for k, a in picks.items() if a.category in FIXTURE_CATEGORIES}
    movables = {k: a for k, a in picks.items() if a.category not in FIXTURE_CATEGORIES}

    instances: list[PlacedInstance] = []
    idx = 0

    # -- fixtures first (support DAG order), biased to occlude the holes ----
    for key, asset in sorted(fixtures.items()):
        src = orig_pose.get(asset.source_object_id or key)
        if stratum.layout == "scanned" and src is not None:
            base_z = float(src.aabb_m.lo[2])  # scanned bottom height, not 0 --
            # a monitor that sat on the table stays at table height
            pose = Pose(position=(src.centroid_m[0], src.centroid_m[1], base_z))
            fp = _fp(asset, pose)
            inst = PlacedInstance(instance_id="", asset=asset, pose=pose, footprint=fp,
                                  top_height=asset.bbox_m[2], support_surface_id="floor",
                                  role="fixture")
        else:
            near = (src.centroid_m[0], src.centroid_m[1]) if src else None
            inst = sample_placement(ws, asset, "floor", rng, near_xy=near, near_radius=1.0)
            if inst is None:
                raise _ComposeReject(f"fixture {key} found no floor placement")
            inst.role = "fixture"
        inst.instance_id = f"{asset.category}_{idx}"
        instances.append(inst)
        ws.placed.append(inst)
        register_asset_surfaces(ws, inst)
        idx += 1

    # -- robot base: a scene variable, sampled and validated ----------------
    base = _place_robot(ws, rng)
    if base is None:
        raise _ComposeReject("no robot base pose with >=0.06 m^2 reachable area")
    ws.robot = base

    # -- manipulables on the primary surface, target reachable BY CONSTRUCTION
    primary = _primary_surface(ws)
    order = sorted(movables.items(), key=lambda kv: -kv[1].bbox_m[0] * kv[1].bbox_m[1])
    target_key = min(movables, key=lambda k: max(movables[k].bbox_m)) if movables else None
    for key, asset in order:
        src = orig_pose.get(asset.source_object_id or key)
        want_reach = key == target_key
        near = None
        if stratum.layout == "scanned" and src is not None:
            near = (src.centroid_m[0], src.centroid_m[1])
        inst = sample_placement(ws, asset, primary, rng,
                                require_reachable=want_reach, near_xy=near,
                                near_radius=0.35)
        if inst is None and primary != "floor":
            inst = sample_placement(ws, asset, "floor", rng, require_reachable=want_reach)
        if inst is None:
            raise _ComposeReject(f"object {key} ({asset.category}) found no valid placement")
        inst.instance_id = f"{asset.category}_{idx}"
        inst.role = "target" if key == target_key else "distractor"
        instances.append(inst)
        ws.placed.append(inst)
        register_asset_surfaces(ws, inst)
        idx += 1

    # receptacle: the largest non-target movable with a support face
    for inst in instances:
        if inst.role == "distractor" and inst.asset.support_faces:
            inst.role = "receptacle"
            break

    placements = [to_placement(inst, i) for i, inst in enumerate(instances)]
    return ScenePayload(
        scene_id=stratum.scene_id,
        kind=stratum.kind,  # type: ignore[arg-type]
        cousin_index=int(stratum.scene_id[1:]),
        split=stratum.split,  # type: ignore[arg-type]
        seed=ctx.seed,
        shell_ref=ArtifactRef(uri="cas://pending", sha256="0" * 64, size_bytes=0,
                              media_type="application/json"),
        render_mode="pure_splat" if stratum.identity == "scanned" else "hybrid_splat_mesh",
        robot=ws.robot,
        placements=placements,
        variations=[VariationRecord(
            axis="identity" if stratum.identity == "swap" else "placement",
            description=f"{stratum.identity} identity, {stratum.layout} layout, "
                        f"task {stratum.task_family}",
            proposer=Proposer(kind="deterministic", detail="stratification_table"),
        )],
    )


def _fp(asset, pose):
    from .compose import footprint_at

    return footprint_at(asset, pose.position[0], pose.position[1], 0.0)


def _primary_surface(ws: Workspace) -> str:
    """Largest surface in the SO-101's comfortable band, else the floor."""
    best, best_area = "floor", 0.0
    for sid, (poly, h, owner) in ws.surfaces.items():
        if owner is not None and 0.3 <= h <= 1.1 and poly.area > best_area:
            best, best_area = sid, poly.area
    return best


def _place_robot(ws: Workspace, rng) -> RobotSpec | None:
    """Base on the primary surface boundary (or floor near the biggest surface),
    validated by reachable-area intersection >= 0.06 m^2."""
    primary = _primary_surface(ws)
    poly, h, owner = ws.surfaces[primary]
    for _ in range(24):
        # boundary strip point
        t = rng.random()
        pt = poly.exterior.interpolate(t, normalized=True)
        base = RobotSpec(base_pose=Pose(position=(float(pt.x), float(pt.y), h)))
        reach = reach_polygon(base, h)
        usable = poly.intersection(reach)
        if usable.area >= 0.06:
            return base
    return None


def _bake(scene: ScenePayload, out) -> ScenePayload:
    placements = []
    for p in scene.placements:
        s = out.settled.get(p.instance_id)
        placements.append(p.model_copy(update={
            "settled_pose": s,
            "settle_translation_m": out.max_translation_m if s else None,
        }))
    return scene.model_copy(update={
        "placements": placements,
        "physics_settle": SettleReport(
            ran=True, sim="mujoco", steps=out.steps, dt=out.dt,
            max_translation_m=out.max_translation_m,
            max_rotation_deg=out.max_rotation_deg,
            max_terminal_speed=out.max_terminal_speed,
            max_penetration_m=out.max_penetration_m,
            exploded=out.exploded,
        ),
    })


def _write_usd(scene, shell, assets, fetch_collision, scratch, degs, ctx):
    from .usda import write_scene_usda

    p = write_scene_usda(scene, shell, assets, lambda a: None, fetch_collision,
                         scratch / scene.scene_id / "scene.usda")
    if p is None:
        if not any(d.code == "SCENE.USD_SKIPPED" for d in degs):
            degs.append(Degradation(
                code="SCENE.USD_SKIPPED", level=DegradationLevel.DEGRADED, stage="cousins",
                message="usd-core unavailable; MJCF is the only scene format",
                remediation="pip install usd-core (present in tools-cpu image)",
            ))
        return None
    return ctx.store.put_file(p, "model/vnd.usda")


def _rej(stratum, l3, l2, gate, detail) -> dict:
    return {"scene": stratum.scene_id, "l3": l3, "l2": l2, "gate": gate, "detail": detail}
