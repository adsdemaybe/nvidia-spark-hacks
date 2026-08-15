"""C4 -- prove Isaac Sim's RTX path actually rasterizes, headless, on aarch64.

Runs inside Isaac Sim's bundled Kit Python:
    ${ISAACSIM_PATH}/python.sh /probes/c4_render_cube.py

Separates two failures that an exit-code check conflates:
  * Kit failed to boot            -> loud, obvious
  * Kit booted and rendered black -> silent, and the classic headless failure

So this asserts on pixel statistics. A uniform frame fails even though the
process exited 0. Writes the PNG to /work so you can look at it when the numbers
disagree with your intuition.
"""
from __future__ import annotations

import sys

MIN_STDDEV = 3.0  # 8-bit levels; a uniform frame is ~0, a lit cube is >>3


def main() -> int:
    try:
        from isaacsim import SimulationApp
    except Exception as exc:
        print(f"FAIL cannot import isaacsim.SimulationApp: {exc}")
        return 1

    app = SimulationApp({"headless": True, "renderer": "RayTracedLighting"})
    try:
        import numpy as np
        import omni.replicator.core as rep
        from pxr import Gf, Usd, UsdGeom, UsdLux

        stage: Usd.Stage = __import__("omni.usd", fromlist=["get_context"]).get_context().get_stage()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)

        cube = UsdGeom.Cube.Define(stage, "/World/Cube")
        cube.CreateSizeAttr(1.0)
        UsdGeom.XformCommonAPI(cube).SetTranslate(Gf.Vec3d(0.0, 0.0, 0.5))

        light = UsdLux.DistantLight.Define(stage, "/World/Key")
        light.CreateIntensityAttr(3000.0)

        cam = rep.create.camera(position=(3.0, 3.0, 2.0), look_at=(0.0, 0.0, 0.5))
        rp = rep.create.render_product(cam, (512, 512))
        rgb = rep.AnnotatorRegistry.get_annotator("rgb")
        rgb.attach(rp)

        # Several steps: the first frames of an RTX render are often incomplete.
        for _ in range(8):
            rep.orchestrator.step()

        frame = np.asarray(rgb.get_data())
        if frame.size == 0:
            print("FAIL annotator returned an empty frame")
            return 1

        gray = frame[..., :3].astype("float32").mean(axis=-1)
        std, mean = float(gray.std()), float(gray.mean())

        try:
            from PIL import Image

            Image.fromarray(frame[..., :3].astype("uint8")).save("/work/c4_cube.png")
        except Exception:
            pass  # writing the artifact is a convenience, not the check

        if std < MIN_STDDEV:
            print(
                f"FAIL frame is uniform (stddev={std:.2f} < {MIN_STDDEV}, mean={mean:.1f}). "
                "Kit booted but RTX did not rasterize -- capture the full Kit log."
            )
            return 1
        print(f"OK rendered {frame.shape[1]}x{frame.shape[0]} stddev={std:.2f} mean={mean:.1f}")
        return 0
    except Exception as exc:
        print(f"FAIL {type(exc).__name__}: {exc}")
        return 1
    finally:
        app.close()


if __name__ == "__main__":
    sys.exit(main())
