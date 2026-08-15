"""pycolmap SfM wrapper. Optional dependency -- imported lazily by stage.py.

CPU SIFT: ~10x slower than CUDA SIFT and entirely acceptable at <=240 frames.
SfM is not where this project's risk lives; if the Spark build of COLMAP fights
back, running this on any CPU box permanently is a fine outcome.
"""
from __future__ import annotations

from pathlib import Path

from r2s.core.schemas import CameraPrior, SfMSummary


def run_pycolmap(images_dir: Path, work_dir: Path) -> SfMSummary:
    import pycolmap

    work_dir.mkdir(parents=True, exist_ok=True)
    db = work_dir / "database.db"
    sparse = work_dir / "sparse"
    if db.exists():
        db.unlink()
    sparse.mkdir(exist_ok=True)

    pycolmap.extract_features(str(db), str(images_dir), camera_model="OPENCV")
    n_imgs = len(list(Path(images_dir).glob("*.png")))
    if n_imgs <= 150:
        pycolmap.match_exhaustive(str(db))
    else:
        pycolmap.match_sequential(str(db))
    recs = pycolmap.incremental_mapping(str(db), str(images_dir), str(sparse))
    if not recs:
        raise RuntimeError(
            "SfM registered zero images. Usual causes: pan instead of orbit "
            "(no parallax), motion blur, featureless scene."
        )
    best = max(recs.values(), key=lambda r: len(r.images))
    best.write(str(sparse / "final"))

    intr: dict[str, CameraPrior] = {}
    for cid, cam in best.cameras.items():
        p = list(cam.params)
        fx, fy, cx, cy = (p[0], p[0], p[1], p[2]) if len(p) < 4 else (p[0], p[1], p[2], p[3])
        intr[str(cid)] = CameraPrior(
            fx=fx, fy=fy, cx=cx, cy=cy, width=cam.width, height=cam.height, source="colmap"
        )

    errs = [img.point2D_count for img in best.images.values()]
    return SfMSummary(
        n_images_input=n_imgs,
        n_images_registered=len(best.images),
        n_points3d=len(best.points3D),
        mean_reproj_error_px=float(best.compute_mean_reprojection_error())
        if hasattr(best, "compute_mean_reprojection_error") else 1.0,
        intrinsics=intr,
        n_submodels=len(recs),
    )
