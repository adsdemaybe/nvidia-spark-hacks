"""C5 -- the make-or-break check: does Isaac render a NuRec gaussian volume
headless on aarch64 GB10?

Run against NVIDIA'S OWN sample, not ours:
    NUREC_SAMPLE=/work/samples/nvidia_scene.usdz \
      ${ISAACSIM_PATH}/python.sh /probes/c5_render_nurec.py

(Samples: the NVIDIA NuRec dataset on HuggingFace -- Voyager Cafe, Galileo Lab,
Wormhole, ZH Lounge.)

Using NVIDIA's asset is the entire point. It separates two questions that are
expensive to confuse:

  * C5 fails -> Isaac cannot render NuRec on this GPU. Closed binary, nothing to
    patch, no second implementation. The architecture changes: USD-only
    validation through pxr, visual checks via 3DGRUT's own playground renderer.
  * C5 passes, C6 (our USDZ) fails -> the bug is provably ours, and tractable.

This is the one dependency whose fallback costs capability rather than time, so
it is bought in the first hours of Spark access -- before any reconstruction
code is written.
"""
from __future__ import annotations

import os
import sys

MIN_STDDEV = 3.0


def main() -> int:
    sample = os.environ.get("NUREC_SAMPLE", "")
    if not sample or not os.path.exists(sample):
        print(f"FAIL NUREC_SAMPLE not set or missing: {sample!r}")
        return 1

    try:
        from isaacsim import SimulationApp
    except Exception as exc:
        print(f"FAIL cannot import isaacsim.SimulationApp: {exc}")
        return 1

    app = SimulationApp({"headless": True, "renderer": "RayTracedLighting"})
    try:
        import numpy as np
        import omni.replicator.core as rep
        import omni.usd
        from pxr import Usd, UsdGeom

        ctx = omni.usd.get_context()
        stage: Usd.Stage = ctx.get_stage()

        ref = UsdGeom.Xform.Define(stage, "/World/Scan")
        if not ref.GetPrim().GetReferences().AddReference(sample):
            print(f"FAIL could not add reference to {sample}")
            return 1

        # Report what actually loaded -- a NuRec asset that resolves to zero
        # prims renders black for a reason that has nothing to do with RTX.
        prims = [p for p in stage.Traverse()]
        typed = {}
        for p in prims:
            t = p.GetTypeName()
            if t:
                typed[str(t)] = typed.get(str(t), 0) + 1
        print(f"  stage: {len(prims)} prims, types={dict(sorted(typed.items())[:8])}")

        bounds = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
        ).ComputeWorldBound(stage.GetPrimAtPath("/World/Scan"))
        rng = bounds.ComputeAlignedRange()
        if rng.IsEmpty():
            centre, dist = (0.0, 0.0, 0.0), 5.0
            print("  warn: reference has an empty bound; using a default camera")
        else:
            mn, mx = rng.GetMin(), rng.GetMax()
            centre = tuple((a + b) / 2.0 for a, b in zip(mn, mx))
            dist = max(max(mx[i] - mn[i] for i in range(3)), 1.0) * 1.2
            print(f"  bounds: min={tuple(round(v,2) for v in mn)} max={tuple(round(v,2) for v in mx)}")

        cam = rep.create.camera(
            position=(centre[0] + dist, centre[1] + dist, centre[2] + dist * 0.5),
            look_at=tuple(centre),
        )
        rp = rep.create.render_product(cam, (768, 768))
        rgb = rep.AnnotatorRegistry.get_annotator("rgb")
        rgb.attach(rp)

        for _ in range(16):  # gaussian volumes need more warmup than a cube
            rep.orchestrator.step()

        frame = np.asarray(rgb.get_data())
        if frame.size == 0:
            print("FAIL annotator returned an empty frame")
            return 1

        gray = frame[..., :3].astype("float32").mean(axis=-1)
        std, mean = float(gray.std()), float(gray.mean())

        try:
            from PIL import Image

            Image.fromarray(frame[..., :3].astype("uint8")).save("/work/c5_nurec.png")
            print("  wrote /work/c5_nurec.png -- LOOK AT IT, do not trust the number alone")
        except Exception:
            pass

        if std < MIN_STDDEV:
            print(
                f"FAIL NuRec volume rendered uniform (stddev={std:.2f} < {MIN_STDDEV}, "
                f"mean={mean:.1f}). If C4 passed and this failed, Isaac's RTX path does not "
                "render NuRec on this GPU -- switch to USD-only validation."
            )
            return 1
        print(f"OK NuRec rendered stddev={std:.2f} mean={mean:.1f} from {os.path.basename(sample)}")
        return 0
    except Exception as exc:
        print(f"FAIL {type(exc).__name__}: {exc}")
        return 1
    finally:
        app.close()


if __name__ == "__main__":
    sys.exit(main())
