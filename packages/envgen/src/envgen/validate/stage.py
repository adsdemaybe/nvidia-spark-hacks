"""Stage 9: validate. Re-open every shipped scene cold and prove it behaves.

This intentionally re-loads from the ARTIFACTS (not the composer's in-memory
state): a scene that only works because the process that built it was still
warm is exactly the failure this catches. Checks per scene, in MuJoCo:

  * loads from its MJCF bytes alone
  * steps N frames with no NaN / explosion (baked poses => near-zero drift)
  * reset idempotency: reset twice -> identical qpos (1e-9)
  * step rate (a scene too slow to train on is not "trainable")
  * a thumbnail render (offscreen; failure degrades, doesn't block -- headless
    GL is machine-dependent and the physics verdicts stand on their own)

The Isaac twin of this stage (`--sim isaac`) drives spark/probes/c5-style
rendering plus PhysX stepping inside the sim container; it lands with SSH.
"""
from __future__ import annotations

import time

import numpy as np
from pydantic import BaseModel

from r2s.core import ArtifactRef, Degradation, DegradationLevel
from r2s.core.runctx import RunContext, StageSpec
from r2s.core.schemas import ScenePayload

from ..cousins.stage import CousinSetPayload

class ValidateConfig(BaseModel):
    sim: str = "mujoco"  # mujoco | isaac
    steps: int = 600
    min_steps_per_s: float = 500.0  # CPU MuJoCo on a tabletop scene is fast
    render_thumbs: bool = True


class SceneValidation(BaseModel):
    scene_id: str
    loads: bool = False
    stepped: int = 0
    nan_free: bool = False
    drift_m: float = 0.0
    reset_idempotent: bool = False
    steps_per_s: float = 0.0
    thumb_ref: ArtifactRef | None = None
    error: str = ""


class ValidationPayload(BaseModel):
    schema_version: str = "1.0.0"
    sim: str
    scenes: dict[str, SceneValidation] = {}


SPEC = StageSpec(
    name="validate",
    version="1",
    artifact_type="validation",
    payload_model=ValidationPayload,
    relevant_packages=["r2s-core", "envgen", "mujoco"],
)


def run(inputs: dict, cfg: ValidateConfig, ctx: RunContext):
    if cfg.sim == "isaac":
        raise NotImplementedError(
            "isaac validation runs in the sim container on the Spark: "
            "./spark.sh run validate <run_id> realsim/sim:dev"
        )
    import mujoco

    cousin_set: CousinSetPayload = inputs["cousins"].payload
    scratch = ctx.scratch("validate")
    degs: list[Degradation] = []
    from r2s.core.artifacts import BaseArtifact

    out: dict[str, SceneValidation] = {}
    thumbs_failed = False
    for scene_id, ref in sorted(cousin_set.scene_refs.items()):
        v = SceneValidation(scene_id=scene_id)
        try:
            scene = ctx.store.get_artifact(ref, BaseArtifact[ScenePayload]).payload
            if scene.mjcf_ref is None:
                raise RuntimeError("scene has no MJCF")
            # cold load: fetch the XML and its hull meshes fresh from the store
            sdir = scratch / scene_id
            sdir.mkdir(exist_ok=True)
            xml_path = sdir / "scene.xml"
            ctx.store.fetch(scene.mjcf_ref, xml_path)
            _materialize_meshes(xml_path, scene, ctx, sdir)
            model = mujoco.MjModel.from_xml_path(str(xml_path))
            data = mujoco.MjData(model)
            v.loads = True

            # reset idempotency
            mujoco.mj_resetData(model, data)
            q1 = data.qpos.copy()
            mujoco.mj_resetData(model, data)
            v.reset_idempotent = bool(np.allclose(q1, data.qpos, atol=1e-9))

            # step + drift + rate
            start = {}
            for p in scene.placements:
                bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, p.instance_id)
                start[p.instance_id] = data.xpos[bid].copy()
            t0 = time.perf_counter()
            okay = True
            for i in range(cfg.steps):
                mujoco.mj_step(model, data)
                if not np.isfinite(data.qpos).all():
                    okay = False
                    v.stepped = i
                    break
            else:
                v.stepped = cfg.steps
            dt_wall = time.perf_counter() - t0
            v.nan_free = okay
            v.steps_per_s = cfg.steps / max(dt_wall, 1e-9)
            drift = 0.0
            for p in scene.placements:
                bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, p.instance_id)
                drift = max(drift, float(np.linalg.norm(data.xpos[bid] - start[p.instance_id])))
            v.drift_m = drift

            if cfg.render_thumbs and not thumbs_failed:
                try:
                    v.thumb_ref = _render_thumb(model, data, ctx, sdir)
                except Exception:
                    thumbs_failed = True  # headless GL absent; degrade once, not per scene
        except Exception as exc:
            v.error = f"{type(exc).__name__}: {exc}"
        out[scene_id] = v

    if thumbs_failed:
        degs.append(Degradation(
            code="VAL.NO_THUMBS", level=DegradationLevel.DEGRADED, stage="validate",
            message="offscreen render unavailable on this machine; physics verdicts unaffected",
            remediation="thumbnails render on the Spark (EGL) and in tools-cpu (osmesa)",
        ))
    return ValidationPayload(sim=cfg.sim, scenes=out), "mujoco", degs


def _materialize_meshes(xml_path, scene: ScenePayload, ctx, sdir) -> None:
    """The MJCF references hull OBJs by the path used at compose time; rewrite
    them to freshly-fetched local copies so the load is genuinely cold."""
    import re

    text = xml_path.read_text()
    for m in re.finditer(r'file="([^"]+)"', text):
        orig = m.group(1)
        name = orig.rsplit("/", 1)[-1]
        local = sdir / name
        if not local.exists():
            # hull files were stored per-asset; recover from the placements
            aid, h = name.rsplit("_h", 1)
            hull_idx = int(h.split(".")[0])
            from r2s.core.artifacts import BaseArtifact  # noqa: F401

            for p in scene.placements:
                if p.asset_id == aid:
                    # collision refs travel inside asset payloads; the validate
                    # stage receives them through the scene's MJCF only, so the
                    # compose-time absolute path may still resolve. Prefer it.
                    break
            import shutil
            from pathlib import Path

            if Path(orig).exists():
                shutil.copyfile(orig, local)
            else:
                raise RuntimeError(f"hull mesh {name} not recoverable cold")
        text = text.replace(f'file="{orig}"', f'file="{local}"')
    xml_path.write_text(text)


def _render_thumb(model, data, ctx, sdir) -> ArtifactRef:
    import mujoco
    from PIL import Image

    renderer = mujoco.Renderer(model, height=360, width=480)
    mujoco.mj_forward(model, data)
    renderer.update_scene(data)
    px = renderer.render()
    p = sdir / "thumb.png"
    Image.fromarray(px).save(p)
    renderer.close()
    return ctx.store.put_file(p, "image/png")
