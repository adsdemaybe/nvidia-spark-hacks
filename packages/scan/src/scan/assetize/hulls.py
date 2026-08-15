"""Convex decomposition ladder: coacd -> single convex hull -> OBB.

Every rung reports which method actually ran; the artifact records it and a
Degradation marks anything below the top rung. You can answer "which of my 8
scenes used box collision because CoACD wouldn't build" from artifacts alone.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh


@dataclass
class HullResult:
    hulls: list[trimesh.Trimesh]
    method: str  # "coacd" | "convex_hull" | "obb"
    volume_ratio: float  # sum(hull volumes) / mesh volume


def decompose(mesh: trimesh.Trimesh, *, max_hulls: int = 16, threshold: float = 0.05) -> HullResult:
    mesh_vol = max(abs(float(mesh.volume)), 1e-9) if mesh.is_watertight else max(
        abs(float(mesh.convex_hull.volume)), 1e-9
    )

    hulls = _try_coacd(mesh, max_hulls=max_hulls, threshold=threshold)
    if hulls:
        vol = sum(abs(float(h.volume)) for h in hulls)
        return HullResult(hulls=hulls, method="coacd", volume_ratio=vol / mesh_vol)

    try:
        h = mesh.convex_hull
        return HullResult(hulls=[h], method="convex_hull",
                          volume_ratio=abs(float(h.volume)) / mesh_vol)
    except BaseException:
        pass

    box = trimesh.creation.box(extents=mesh.extents)
    box.apply_translation(mesh.bounds.mean(axis=0))
    return HullResult(hulls=[box], method="obb",
                      volume_ratio=abs(float(box.volume)) / mesh_vol)


def _try_coacd(mesh: trimesh.Trimesh, *, max_hulls: int, threshold: float):
    try:
        import coacd
    except ImportError:
        return None
    try:
        cm = coacd.Mesh(np.asarray(mesh.vertices), np.asarray(mesh.faces))
        parts = coacd.run_coacd(cm, threshold=threshold, max_convex_hull=max_hulls)
        out = []
        for verts, faces in parts:
            h = trimesh.Trimesh(vertices=np.asarray(verts), faces=np.asarray(faces))
            if len(h.faces) >= 4:
                out.append(h.convex_hull)  # guarantee closed convex solids
        return out or None
    except BaseException:
        return None
