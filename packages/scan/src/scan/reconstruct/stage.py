"""Stage 2: reconstruct. capture -> posed splat in a gravity-aligned metric frame.

Real work on any machine: SfM (pycolmap when importable), sim(3) scale solve
against the LiDAR mesh, floor fit, gravity canonicalization. GPU-only work
(3DGRUT training) hides behind the backend protocol.
"""
from __future__ import annotations

import tarfile
import tempfile
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from r2s.core import Degradation, DegradationLevel, assumed, measured
from r2s.core.runctx import RunContext, StageSpec
from r2s.core.schemas import (
    CapturePayload,
    NuRecExport,
    ReconstructionPayload,
    ScaleSolution,
    SfMSummary,
    SplatSummary,
)

from .backends import select_backend
from .gravity import fit_floor, gravity_align_transform
from .ply import save_ply
from .scale import solve_sim3_icp

SPEC = StageSpec(
    name="reconstruct",
    version="1",
    artifact_type="reconstruction",
    payload_model=ReconstructionPayload,
    relevant_packages=["r2s-core", "scan", "numpy", "scipy"],
)


class ReconstructConfig(BaseModel):
    backend: str = "stub"  # three_dgrut | stub (gsplat lands with the Spark)
    fixture_dir: str | None = None
    iterations: int = 15000
    config_name: str = "apps/colmap_3dgut_mcmc.yaml"
    assume_scale: bool = False  # explicit escape hatch; stamps ASSUMED + DEGRADED


def _try_sfm(images_dir: Path, scratch: Path) -> SfMSummary | None:
    """pycolmap when importable; None otherwise (stub fills in synthetics).
    The COLMAP-CLI fallback lands with the Spark image, where the binary exists."""
    try:
        import pycolmap  # noqa: F401
    except ImportError:
        return None
    from .sfm_colmap import run_pycolmap  # local import: optional dep

    return run_pycolmap(images_dir, scratch)


def run(inputs: dict, cfg: ReconstructConfig, ctx: RunContext):
    capture = inputs["capture"]
    cap: CapturePayload = capture.payload
    scratch = ctx.scratch("reconstruct")
    degs: list[Degradation] = []
    rng = ctx.rng("reconstruct")

    frames_dir = scratch / "images"
    if not frames_dir.exists():
        ctx.store.fetch_dir(cap.frames.dir_ref, frames_dir)

    # --- SfM -------------------------------------------------------------
    sfm = _try_sfm(frames_dir, scratch / "colmap")
    if sfm is None:
        degs.append(Degradation(
            code="RECON.SFM_STUBBED", level=DegradationLevel.STUB, stage="reconstruct",
            message="pycolmap unavailable; SfM summary is synthetic",
            remediation="run on the Spark (colmap in recon-gpu image) or pip install pycolmap",
        ))
        sfm = SfMSummary(
            n_images_input=cap.frames.count,
            n_images_registered=cap.frames.count,
            n_points3d=0,
            mean_reproj_error_px=0.8,
        )

    # --- splat training --------------------------------------------------
    backend = select_backend(cfg.backend, Path(cfg.fixture_dir) if cfg.fixture_dir else None)
    okay, why = backend.available()
    if not okay:
        raise RuntimeError(f"splat backend {backend.name} unavailable: {why}")
    result = backend.train(scratch / "colmap", frames_dir, scratch / "splat",
                           cfg.model_dump())
    cloud = result.cloud

    # --- metric scale ----------------------------------------------------
    if cap.lidar is not None and len(cloud) > 0:
        with tempfile.TemporaryDirectory() as td:
            mesh_path = Path(td) / "lidar.bin"
            ctx.store.fetch(cap.lidar.mesh_ref, mesh_path)
            import trimesh

            lmesh = trimesh.load(mesh_path, force="mesh", file_type=cap.lidar.format)
            lidar_pts = np.asarray(
                lmesh.sample(5000) if hasattr(lmesh, "sample") else lmesh.vertices
            )
        sim3 = solve_sim3_icp(cloud.means, lidar_pts, rng, lidar_mesh=lmesh)
        scale = ScaleSolution(
            scale_factor=measured(
                sim3.scale, "1", source=f"lidar_sim3 rmse={sim3.rmse_m:.4f}m",
                stddev=None,
            ),
            method="lidar_sim3",
            inlier_ratio=sim3.inlier_ratio,
            rmse_m=sim3.rmse_m,
        )
        T_metric = sim3.T
    else:
        if not cfg.assume_scale and cap.lidar is None:
            degs.append(Degradation(
                code="RECON.SCALE_ASSUMED", level=DegradationLevel.DEGRADED,
                stage="reconstruct",
                message="no LiDAR anchor; scale=1.0 assumed",
                remediation="supply --lidar at capture, or pass assume_scale=true to accept this",
            ))
        scale = ScaleSolution(scale_factor=assumed(1.0, "1", note="no metric anchor"), method="assumed")
        T_metric = np.eye(4)

    metric_cloud = cloud.transformed(T_metric)

    # --- gravity / floor -------------------------------------------------
    floor = fit_floor(metric_cloud.means, rng) if len(metric_cloud) else None
    if floor is not None:
        T_grav = gravity_align_transform(floor)
        metric_cloud = metric_cloud.transformed(T_grav)
        T_metric = T_grav @ T_metric
        floor = fit_floor(metric_cloud.means, rng)  # re-fit in the aligned frame

    ply_out = scratch / "metric_gaussians.ply"
    save_ply(metric_cloud, ply_out)
    ply_ref = ctx.store.put_file(ply_out, "application/x-ply")

    # --- NuRec export (backend-provided; verify by opening, never trust flags)
    nurec = None
    if result.nurec_usdz and result.nurec_usdz.exists():
        n_prims, opened = _probe_usd(result.nurec_usdz)
        nurec = NuRecExport(
            usd_ref=ctx.store.put_file(result.nurec_usdz, "model/vnd.usdz+zip"),
            loaded_by_usd_core=opened,
            n_prims=n_prims,
            exporter_flag_used="export_usd.enabled",
        )

    ext = (
        metric_cloud.means.max(0) - metric_cloud.means.min(0)
        if len(metric_cloud) else np.zeros(3)
    )
    payload = ReconstructionPayload(
        capture_ref=_ref_of(capture),
        sfm=sfm,
        splat=SplatSummary(
            method=result.method,  # type: ignore[arg-type]
            config_name=cfg.config_name,
            n_gaussians=len(metric_cloud),
            iterations=result.iterations,
            val_psnr=result.val_psnr,
            val_ssim=result.val_ssim,
            val_split=result.val_split,
            ply_ref=ply_ref,
        ),
        scale=scale,
        floor_plane=floor,
        metric_transform=[float(v) for v in T_metric.reshape(-1)],
        nurec=nurec,
        room_extent_m=(float(ext[0]), float(ext[1]), float(ext[2])),
    )
    return payload, backend.name, degs


def _probe_usd(path: Path) -> tuple[int, bool]:
    try:
        from pxr import Usd

        stage = Usd.Stage.Open(str(path))
        return sum(1 for _ in stage.Traverse()), True
    except Exception:
        return 0, False


def _ref_of(art) -> "object":
    from r2s.core import ArtifactRef

    return ArtifactRef(
        uri=f"cas://sha256/{art.header.artifact_id}",
        sha256=art.header.artifact_id,
        size_bytes=0,
        media_type="application/json",
    )
