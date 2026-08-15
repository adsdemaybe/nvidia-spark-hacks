"""3DGS-convention gaussian PLY I/O (the format 3DGRUT/gsplat/viewers share).

Fields: x,y,z, f_dc_0..2 (SH0 color), opacity (pre-sigmoid), scale_0..2 (log),
rot_0..3 (wxyz). We only need means/colors/opacity/scales for segmentation and
shell work; the rest round-trips untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class GaussianCloud:
    means: np.ndarray  # (N,3) float32
    colors: np.ndarray  # (N,3) float32, SH0-space
    opacity: np.ndarray  # (N,) float32, pre-sigmoid
    scales: np.ndarray  # (N,3) float32, log-space
    quats: np.ndarray  # (N,4) float32, wxyz

    def __len__(self) -> int:
        return len(self.means)

    def subset(self, idx: np.ndarray) -> GaussianCloud:
        return GaussianCloud(
            self.means[idx], self.colors[idx], self.opacity[idx],
            self.scales[idx], self.quats[idx],
        )

    def transformed(self, T: np.ndarray) -> GaussianCloud:
        """Apply a 4x4 sim(3)-ish transform to means (rotation/scale of the
        per-gaussian covariances is deferred until someone renders them --
        segmentation and shell fitting only consume means)."""
        R, t = T[:3, :3], T[:3, 3]
        return GaussianCloud(
            (self.means @ R.T + t).astype(np.float32),
            self.colors, self.opacity, self.scales, self.quats,
        )


def save_ply(cloud: GaussianCloud, path: Path) -> None:
    from plyfile import PlyData, PlyElement

    n = len(cloud)
    names = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
             "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    arr = np.empty(n, dtype=[(name, "f4") for name in names])
    arr["x"], arr["y"], arr["z"] = cloud.means.T
    arr["f_dc_0"], arr["f_dc_1"], arr["f_dc_2"] = cloud.colors.T
    arr["opacity"] = cloud.opacity
    arr["scale_0"], arr["scale_1"], arr["scale_2"] = cloud.scales.T
    arr["rot_0"], arr["rot_1"], arr["rot_2"], arr["rot_3"] = cloud.quats.T
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    PlyData([PlyElement.describe(arr, "vertex")], text=False).write(str(path))


def load_ply(path: Path) -> GaussianCloud:
    from plyfile import PlyData

    v = PlyData.read(str(path))["vertex"]

    def col(*names, default=None):
        present = [n for n in names if n in v.data.dtype.names]
        if not present:
            if default is None:
                raise KeyError(f"PLY missing {names}")
            return np.full((len(v.data), len(names)), default, dtype=np.float32)
        return np.stack([np.asarray(v[n], dtype=np.float32) for n in present], axis=-1)

    means = col("x", "y", "z")
    colors = col("f_dc_0", "f_dc_1", "f_dc_2", default=0.5)
    opacity = col("opacity", default=2.0)[:, 0]
    scales = col("scale_0", "scale_1", "scale_2", default=-4.0)
    quats = col("rot_0", "rot_1", "rot_2", "rot_3", default=0.0)
    if not np.any(quats):
        quats[:, 0] = 1.0
    return GaussianCloud(means, colors, opacity, scales, quats)
