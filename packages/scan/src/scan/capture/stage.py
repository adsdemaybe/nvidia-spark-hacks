"""Stage 1: capture. video|frame-dir (+ optional LiDAR mesh) -> CaptureArtifact."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from r2s.core import Degradation, DegradationLevel, inferred
from r2s.core.runctx import RunContext, StageSpec
from r2s.core.schemas import CapturePayload, FrameSet, LidarScan

from .frames import exif_intrinsics_prior, extract_frames, ingest_frame_dir

SPEC = StageSpec(
    name="capture",
    version="1",
    artifact_type="capture",
    payload_model=CapturePayload,
    relevant_packages=["r2s-core", "scan", "opencv-python-headless"],
)


class CaptureConfig(BaseModel):
    video: str
    lidar: str | None = None
    fps: float = 3.0
    max_width: int = 1600
    max_frames: int = 240
    sharp_frac: float = 0.75


def run(inputs: dict, cfg: CaptureConfig, ctx: RunContext):
    import numpy as np

    src = Path(cfg.video)
    scratch = ctx.scratch("capture")
    degs: list[Degradation] = []

    if src.is_dir():
        fr = ingest_frame_dir(src, scratch, max_frames=cfg.max_frames)
        video_ref = None
    else:
        fr = extract_frames(
            src, scratch,
            fps=cfg.fps, max_width=cfg.max_width,
            max_frames=cfg.max_frames, sharp_frac=cfg.sharp_frac,
        )
        video_ref = ctx.store.put_file(src, "video/mp4")

    frames_ref = ctx.store.put_dir(fr.frames_dir)
    sharp = np.asarray(fr.sharpness)

    lidar = None
    if cfg.lidar:
        lp = Path(cfg.lidar)
        try:
            import trimesh

            mesh = trimesh.load(lp, force="mesh")
            ext = mesh.extents
            lidar = LidarScan(
                mesh_ref=ctx.store.put_file(lp, "model/mesh"),
                format=lp.suffix.lstrip(".").lower(),  # type: ignore[arg-type]
                n_vertices=len(mesh.vertices),
                aabb_m=(float(ext[0]), float(ext[1]), float(ext[2])),
            )
        except Exception as exc:
            degs.append(Degradation(
                code="CAP.LIDAR_UNREADABLE",
                level=DegradationLevel.DEGRADED,
                stage="capture",
                message=f"LiDAR mesh {lp} unreadable: {exc}",
                remediation="re-export from Polycam/Scaniverse as OBJ or GLB",
            ))
    else:
        # No metric anchor: scale will be ASSUMED and everything downstream
        # is stamped DEGRADED. Recorded here so the cause is visible at the source.
        degs.append(Degradation(
            code="CAP.NO_LIDAR",
            level=DegradationLevel.DEGRADED,
            stage="capture",
            message="no LiDAR mesh supplied; metric scale will be assumed",
            remediation="capture a Polycam/Scaniverse scan alongside the video",
        ))

    payload = CapturePayload(
        video_ref=video_ref,
        video_duration_s=fr.video_duration_s,
        video_fps=fr.video_fps,
        frames=FrameSet(
            dir_ref=frames_ref,
            count=fr.count,
            width=fr.width,
            height=fr.height,
            fps_sampled=fr.fps_sampled,
            sharpness_p10=float(np.percentile(sharp, 10)) if len(sharp) else 0.0,
            sharpness_median=float(np.median(sharp)) if len(sharp) else 0.0,
            dropped_blurry=fr.dropped_blurry,
            dropped_subsample=fr.dropped_subsample,
            exposure_cv=fr.exposure_cv,
        ),
        intrinsics_prior=exif_intrinsics_prior(src, fr.width, fr.height),
        lidar=lidar,
        orbit_coverage_deg=inferred(
            360.0 * min(fr.video_duration_s / 60.0, 1.0) if fr.video_duration_s else 0.0,
            "deg",
            method="duration_heuristic_until_feature_flow_lands",
        ),
    )
    return payload, "ffmpeg", degs
