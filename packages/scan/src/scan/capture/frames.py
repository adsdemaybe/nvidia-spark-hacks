"""Video -> sharp, deduplicated, downscaled frames. Fully real, CPU-only.

Ported from the earlier splatsim prototype: oversample with ffmpeg, score every
frame with Laplacian variance, drop the blurriest, then subsample uniformly so
coverage stays even along the camera path instead of clustering wherever the
user held still. Phone clips are rolling-shutter + motion-blurred; blurry
frames poison SfM.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class FrameResult:
    frames_dir: Path
    count: int
    width: int
    height: int
    fps_sampled: float
    sharpness: list[float]
    dropped_blurry: int
    dropped_subsample: int
    exposure_cv: float
    video_duration_s: float
    video_fps: float


def _ffmpeg_bin() -> str:
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    try:  # bundled static binary; ships aarch64 builds too
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError("no ffmpeg on PATH and imageio-ffmpeg not installed") from exc


def _probe(video: Path) -> tuple[float, float]:
    """(duration_s, fps) via ffprobe if present, else parsed from ffmpeg -i."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=avg_frame_rate:format=duration",
                 "-of", "csv=p=0", str(video)],
                capture_output=True, text=True, check=True,
            ).stdout.split()
            num, den = out[0].split("/")
            fps = float(num) / max(float(den), 1.0)
            return float(out[1]), fps
        except Exception:
            pass
    return 0.0, 0.0


def extract_frames(
    video: Path,
    out_dir: Path,
    *,
    fps: float = 3.0,
    max_width: int = 1600,
    max_frames: int = 240,
    sharp_frac: float = 0.75,
) -> FrameResult:
    import cv2

    video = Path(video)
    out_dir = Path(out_dir)
    raw = out_dir / "_raw"
    if raw.exists():
        shutil.rmtree(raw)
    raw.mkdir(parents=True)

    duration, native_fps = _probe(video)
    # scale=-2 keeps even dimensions, required by most decoders
    vf = f"fps={fps},scale='min({max_width},iw)':-2"
    subprocess.run(
        [_ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(video),
         "-vf", vf, "-fps_mode", "vfr", str(raw / "%05d.png")],
        check=True,
    )
    files = sorted(glob.glob(str(raw / "*.png")))
    if not files:
        raise RuntimeError(f"ffmpeg produced no frames from {video} -- is it readable?")

    scores, means = [], []
    for f in files:
        g = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
        scores.append(float(cv2.Laplacian(g, cv2.CV_64F).var()))
        means.append(float(g.mean()))
    scores_a = np.asarray(scores)
    means_a = np.asarray(means)
    exposure_cv = float(means_a.std() / max(means_a.mean(), 1e-6))

    keep_n = max(20, int(len(files) * sharp_frac))
    thresh = np.sort(scores_a)[::-1][min(keep_n, len(scores_a)) - 1]
    keep = [(f, s) for f, s in zip(files, scores, strict=True) if s >= thresh]
    dropped_blurry = len(files) - len(keep)

    dropped_sub = 0
    if len(keep) > max_frames:
        idx = np.linspace(0, len(keep) - 1, max_frames).round().astype(int)
        dropped_sub = len(keep) - max_frames
        keep = [keep[i] for i in idx]

    images = out_dir / "images"
    if images.exists():
        shutil.rmtree(images)
    images.mkdir(parents=True)
    kept_scores = []
    for i, (f, s) in enumerate(keep):
        shutil.copyfile(f, images / f"{i:05d}.png")
        kept_scores.append(s)
    shutil.rmtree(raw, ignore_errors=True)

    h, w = cv2.imread(str(images / "00000.png"), cv2.IMREAD_GRAYSCALE).shape[:2]
    return FrameResult(
        frames_dir=images,
        count=len(keep),
        width=w,
        height=h,
        fps_sampled=fps,
        sharpness=kept_scores,
        dropped_blurry=dropped_blurry,
        dropped_subsample=dropped_sub,
        exposure_cv=exposure_cv,
        video_duration_s=duration,
        video_fps=native_fps,
    )


def ingest_frame_dir(src: Path, out_dir: Path, *, max_frames: int = 240) -> FrameResult:
    """Fixture path: a directory of PNGs is already 'extracted'."""
    import cv2

    files = sorted(Path(src).glob("*.png"))
    if not files:
        raise RuntimeError(f"no PNGs in {src}")
    if len(files) > max_frames:
        idx = np.linspace(0, len(files) - 1, max_frames).round().astype(int)
        files = [files[i] for i in idx]

    images = Path(out_dir) / "images"
    if images.exists():
        shutil.rmtree(images)
    images.mkdir(parents=True)
    scores, means = [], []
    for i, f in enumerate(files):
        shutil.copyfile(f, images / f"{i:05d}.png")
        g = cv2.imread(str(images / f"{i:05d}.png"), cv2.IMREAD_GRAYSCALE)
        scores.append(float(cv2.Laplacian(g, cv2.CV_64F).var()))
        means.append(float(g.mean()))
    h, w = cv2.imread(str(images / "00000.png"), cv2.IMREAD_GRAYSCALE).shape[:2]
    means_a = np.asarray(means)
    return FrameResult(
        frames_dir=images,
        count=len(files),
        width=w,
        height=h,
        fps_sampled=0.0,
        sharpness=scores,
        dropped_blurry=0,
        dropped_subsample=0,
        exposure_cv=float(means_a.std() / max(means_a.mean(), 1e-6)),
        video_duration_s=0.0,
        video_fps=0.0,
    )


def exif_intrinsics_prior(video_or_frame: Path, width: int, height: int):
    """Focal-length prior from EXIF when present; a plausible default otherwise.
    SfM refines this -- it only needs to be in the right ballpark to converge."""
    from r2s.core.schemas import CameraPrior

    f35 = None
    try:
        from PIL import ExifTags, Image

        if video_or_frame.suffix.lower() in (".jpg", ".jpeg", ".png", ".heic"):
            img = Image.open(video_or_frame)
            exif = img.getexif()
            for k, v in exif.items():
                if ExifTags.TAGS.get(k) == "FocalLengthIn35mmFilm":
                    f35 = float(v)
    except Exception:
        pass

    if f35:
        fx = width * f35 / 36.0
        src = "exif"
    else:
        # ~62deg horizontal FOV: the ballpark for a phone main camera
        fx = 0.83 * width
        src = "assumed"
    return CameraPrior(
        fx=fx, fy=fx, cx=width / 2.0, cy=height / 2.0,
        width=width, height=height, source=src,
    )
