"""Stage 5: generate. Scanned categories -> a library of substitute assets.

For each scanned category: K candidate substitutes (identity axis fuel), plus a
couple of distractor categories. Every candidate flows through the SAME
assetize path as scanned objects, then the index-time filters run:

  * graspability (SO-101 gripper width + payload) for manipulable categories --
    filtered HERE, not at cousin-gate time, or generation "mysteriously" yields
    5 candidates and rejects all 5;
  * scale clamp [0.5, 2.0] against the scanned counterpart, uniform only.

Descriptor variation is the ONLY place an LLM may act in this stage (propose
"a low wooden stool" vs "a metal bar stool"); with propose=False, deterministic
template descriptors are used and CI never calls a model.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from r2s.core import AssetProvenance, Degradation, DegradationLevel, Proposer
from r2s.core.runctx import RunContext, StageSpec
from r2s.core.schemas import AssetBundlePayload, SegmentationPayload

from ..assetize.stage import AssetizeConfig, assetize_mesh
from .sources import cache_key, select_source

SPEC = StageSpec(
    name="generate",
    version="1",
    artifact_type="asset_bundle",
    payload_model=AssetBundlePayload,
    relevant_packages=["r2s-core", "scan", "trimesh"],
)

# categories whose instances a policy might manipulate -> graspability filter on
MANIPULABLE = {"mug", "cup", "bottle", "can", "box", "book", "mouse", "blob"}
DISTRACTOR_CATEGORIES = ["book", "can"]

_TEMPLATES = [
    "a small {c}", "a large {c}", "a wooden {c}", "a metal {c}",
    "a plastic {c}", "a modern {c}", "an old worn {c}", "a colorful {c}",
]


class GenerateConfig(BaseModel):
    source: str = "procedural"  # hunyuan3d | fixture | procedural
    fixture_root: str | None = None
    candidates_per_category: int = 5  # top-K pool; cousins sample WITHOUT replacement
    scale_clamp: tuple[float, float] = (0.5, 2.0)
    propose: bool = False


def run(inputs: dict, cfg: GenerateConfig, ctx: RunContext):
    seg = inputs["segment"]
    sp: SegmentationPayload = seg.payload
    scratch = ctx.scratch("generate")
    cache_dir = scratch / "gen_cache"
    cache_dir.mkdir(exist_ok=True)
    degs: list[Degradation] = []

    source = select_source(cfg.source, Path(cfg.fixture_root) if cfg.fixture_root else None)
    okay, why = source.available()
    if not okay:
        # descend the ladder loudly, never silently
        degs.append(Degradation(
            code="GEN.SOURCE_FALLBACK", level=DegradationLevel.DEGRADED, stage="generate",
            message=f"source {cfg.source} unavailable ({why}); using procedural",
            remediation="hunyuan3d lands with the Spark; fixture needs fixtures/assets/",
        ))
        source = select_source("procedural", None)

    # scanned categories + sizes to match against. Two anchor dims per object:
    # (horizontal max, height) -- support furniture matches HEIGHT (functional:
    # a table at the wrong height makes every task on it unreachable),
    # manipulables match the largest horizontal extent (the gripper's view).
    SUPPORT_ANCHORED = {"table", "desk", "chair", "shelf", "lamp", "monitor"}
    scanned: dict[str, list[tuple[float, float]]] = {}
    for o in sp.objects:
        ext = o.aabb_m.extents
        scanned.setdefault(o.label, []).append((max(ext[0], ext[1]), ext[2]))
    categories = sorted(scanned) + [c for c in DISTRACTOR_CATEGORIES if c not in scanned]

    aconf = AssetizeConfig()
    assets, dropped = {}, {}
    proposer = Proposer(
        kind="default", detail=f"template_descriptors[{source.name}]"
    )

    for cat in categories:
        if cat in scanned:
            dim_idx = 1 if cat in SUPPORT_ANCHORED else 0
            dims = sorted(d[dim_idx] for d in scanned[cat])
            target_dim = dims[len(dims) // 2]
        else:
            target_dim = None
        made = 0
        for k in range(cfg.candidates_per_category * 2):  # 2x attempts for the K quota
            if made >= cfg.candidates_per_category:
                break
            seed = int(ctx.rng("generate", cat, k).integers(0, 2**31))
            desc = _TEMPLATES[k % len(_TEMPLATES)].format(c=cat)
            aid = f"gen_{cat}_{k}"
            try:
                mesh = source.produce(cat, desc, seed, cache_dir)
            except Exception as exc:
                dropped[aid] = f"generation failed: {exc}"
                continue

            # uniform rescale toward the scanned counterpart, clamped.
            # Anchor: height for support furniture, largest horizontal extent
            # for manipulables (gripper's-eye view).
            if target_dim is not None and not mesh.is_empty:
                ext = mesh.extents
                cand_dim = float(ext[2]) if cat in SUPPORT_ANCHORED \
                    else float(max(ext[0], ext[1]))
                if cand_dim > 1e-6:
                    s = target_dim / cand_dim
                    lo, hi = cfg.scale_clamp
                    if not (lo <= s <= hi):
                        dropped[aid] = f"scale {s:.2f} outside [{lo},{hi}]"
                        continue
                    mesh = mesh.copy()
                    mesh.apply_scale(s)

            provenance = (AssetProvenance.GENERATED if source.name == "hunyuan3d"
                          else AssetProvenance.PROCEDURAL)
            payload, drop = assetize_mesh(
                mesh, asset_id=aid, label=cat, category=cat,
                provenance=provenance, proposer=proposer, cfg=aconf, ctx=ctx,
                descriptor=desc,
            )
            if payload is None:
                dropped[aid] = drop or "assetize failed"
                continue
            payload = payload.model_copy(update={
                "generation_cache_key": cache_key(source.name, cat, desc, seed),
            })
            # index-time graspability filter for manipulables
            if cat in MANIPULABLE and not payload.fits_so101_gripper:
                dropped[aid] = (
                    f"fails SO-101 filter (width="
                    f"{payload.graspable_width_m.value if payload.graspable_width_m else None}, "
                    f"mass={payload.physics.mass.value:.3f}kg)"
                )
                continue
            assets[aid] = payload
            made += 1

        if cat in scanned and made == 0:
            degs.append(Degradation(
                code="GEN.CATEGORY_EMPTY", level=DegradationLevel.DEGRADED, stage="generate",
                message=f"no viable substitutes for scanned category {cat!r}",
                remediation="cousins fall back to reusing the scanned asset on the identity axis",
            ))

    payload = AssetBundlePayload(asset_ids=sorted(assets), assets=assets, dropped=dropped)
    return payload, source.name, degs
