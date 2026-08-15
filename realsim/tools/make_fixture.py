"""Deterministic synthetic-fixture generator for the real-to-sim pipeline.

Produces fixtures/tiny_room/:
  gaussians.ply  - 3DGS-convention gaussian cloud of a synthetic room (metric, z-up, floor z=0)
  clusters.json  - per-object PLY row indices + movability
  lidar.glb      - box-room mesh in the same frame (sim(3) vs the cloud ~ identity)
  frames/        - 45 sharp synthetic PNGs (480x360) passing the Laplacian-variance gate
  fixture.json   - stub metrics + seed

Run: cd <repo root> && uv run python tools/make_fixture.py
Deterministic: everything is drawn from one np.random.Generator(PCG64(SEED)).
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from scan.reconstruct.ply import GaussianCloud, save_ply

SEED = 20260814
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "fixtures" / "tiny_room"

# Room: 3.0 x 2.4 footprint, walls 2.3 m, floor at z=0, centered on origin.
RX, RY, RZ = 1.5, 1.2, 2.3  # half-extents in x/y, wall height in z
NOISE = 0.003  # 3 mm

TABLE_CX, TABLE_CY = 0.8, 0.0
TABLE_W, TABLE_D = 1.2, 0.6  # footprint x, y
TABLE_TOP = 0.72
TABLE_THICK = 0.03
LEG_SIZE = 0.05


def sample_rect(rng, n, axis, value, lo0, hi0, lo1, hi1):
    """n points on an axis-aligned rectangle; `axis` is the constant coordinate."""
    pts = np.empty((n, 3))
    free = [i for i in range(3) if i != axis]
    pts[:, axis] = value
    pts[:, free[0]] = rng.uniform(lo0, hi0, n)
    pts[:, free[1]] = rng.uniform(lo1, hi1, n)
    return pts


def sample_box_surface(rng, n, center, size):
    """n points on the surface of an axis-aligned box, proportional to face area."""
    cx, cy, cz = center
    sx, sy, sz = size
    faces = [  # (axis, sign, area)
        (0, -1, sy * sz), (0, +1, sy * sz),
        (1, -1, sx * sz), (1, +1, sx * sz),
        (2, -1, sx * sy), (2, +1, sx * sy),
    ]
    areas = np.array([f[2] for f in faces])
    counts = rng.multinomial(n, areas / areas.sum())
    half = np.array([sx, sy, sz]) / 2.0
    c = np.array([cx, cy, cz])
    parts = []
    for (axis, sign, _), k in zip(faces, counts):
        if k == 0:
            continue
        p = rng.uniform(-half, half, (k, 3))
        p[:, axis] = sign * half[axis]
        parts.append(p + c)
    return np.concatenate(parts, axis=0)


def sample_cylinder(rng, n, center_xy, base_z, r, h):
    """n points on lateral surface + top/bottom disks, proportional to area."""
    lat = 2 * math.pi * r * h
    disk = math.pi * r * r
    counts = rng.multinomial(n, np.array([lat, disk, disk]) / (lat + 2 * disk))
    parts = []
    if counts[0]:
        th = rng.uniform(0, 2 * math.pi, counts[0])
        z = rng.uniform(0, h, counts[0])
        parts.append(np.stack([r * np.cos(th), r * np.sin(th), z], axis=1))
    for i, zc in ((1, h), (2, 0.0)):
        if counts[i]:
            th = rng.uniform(0, 2 * math.pi, counts[i])
            rr = r * np.sqrt(rng.uniform(0, 1, counts[i]))
            parts.append(np.stack([rr * np.cos(th), rr * np.sin(th),
                                   np.full(counts[i], zc)], axis=1))
    pts = np.concatenate(parts, axis=0)
    pts[:, 0] += center_xy[0]
    pts[:, 1] += center_xy[1]
    pts[:, 2] += base_z
    return pts


def build_cloud(rng):
    """Returns (GaussianCloud, {name: (start, stop)} index ranges)."""
    parts = []  # (name, points, rgb)

    # --- room shell ---
    parts.append(("floor", sample_rect(rng, 15000, 2, 0.0, -RX, RX, -RY, RY),
                  (0.55, 0.50, 0.45)))
    parts.append(("wall_xneg", sample_rect(rng, 4000, 0, -RX, -RY, RY, 0.0, RZ),
                  (0.85, 0.84, 0.80)))
    parts.append(("wall_xpos", sample_rect(rng, 4000, 0, RX, -RY, RY, 0.0, RZ),
                  (0.85, 0.84, 0.80)))
    parts.append(("wall_yneg", sample_rect(rng, 4000, 1, -RY, -RX, RX, 0.0, RZ),
                  (0.82, 0.83, 0.86)))
    parts.append(("wall_ypos", sample_rect(rng, 4000, 1, RY, -RX, RX, 0.0, RZ),
                  (0.82, 0.83, 0.86)))

    # --- table: top slab + 4 legs, ~3000 pts total ---
    top = sample_box_surface(
        rng, 2200,
        (TABLE_CX, TABLE_CY, TABLE_TOP - TABLE_THICK / 2),
        (TABLE_W, TABLE_D, TABLE_THICK))
    leg_h = TABLE_TOP - TABLE_THICK
    leg_pts = []
    inset = 0.06
    for sx in (-1, 1):
        for sy in (-1, 1):
            lx = TABLE_CX + sx * (TABLE_W / 2 - inset)
            ly = TABLE_CY + sy * (TABLE_D / 2 - inset)
            leg_pts.append(sample_box_surface(
                rng, 200, (lx, ly, leg_h / 2), (LEG_SIZE, LEG_SIZE, leg_h)))
    parts.append(("table", np.concatenate([top] + leg_pts, axis=0),
                  (0.45, 0.30, 0.18)))

    # --- monitor: 0.55 x 0.05 x 0.32 box at the back edge of the table ---
    mon_size = (0.55, 0.05, 0.32)
    mon_center = (TABLE_CX, TABLE_CY + TABLE_D / 2 - mon_size[1] / 2 - 0.02,
                  TABLE_TOP + mon_size[2] / 2)
    parts.append(("monitor", sample_box_surface(rng, 1200, mon_center, mon_size),
                  (0.08, 0.08, 0.10)))

    # --- mug: cylinder r=0.04 h=0.1 on the table top ---
    parts.append(("mug", sample_cylinder(rng, 500, (0.6, 0.15), TABLE_TOP, 0.04, 0.1),
                  (0.75, 0.15, 0.12)))

    # --- book: 0.2 x 0.15 x 0.03 box on the table top ---
    parts.append(("book", sample_box_surface(
        rng, 600, (0.95, -0.15, TABLE_TOP + 0.015), (0.2, 0.15, 0.03)),
        (0.15, 0.35, 0.60)))

    means, colors, ranges = [], [], {}
    offset = 0
    for name, pts, rgb in parts:
        pts = pts + rng.normal(0.0, NOISE, pts.shape)  # 3 mm sensor-ish noise
        means.append(pts)
        col = np.tile(np.asarray(rgb), (len(pts), 1))
        col += rng.normal(0.0, 0.02, col.shape)  # slight per-splat color variation
        colors.append(np.clip(col, 0.0, 1.0))
        ranges[name] = (offset, offset + len(pts))
        offset += len(pts)

    means = np.concatenate(means, axis=0).astype(np.float32)
    colors = np.concatenate(colors, axis=0).astype(np.float32)
    n = len(means)
    opacity = np.full(n, 2.0, dtype=np.float32)
    scales = np.full((n, 3), math.log(0.01), dtype=np.float32)
    quats = np.zeros((n, 4), dtype=np.float32)
    quats[:, 0] = 1.0
    return GaussianCloud(means, colors, opacity, scales, quats), ranges


def build_lidar_glb(path):
    import trimesh

    t = 0.02  # slab thickness
    boxes = []

    def box(size, center):
        b = trimesh.creation.box(extents=size)
        b.apply_translation(center)
        boxes.append(b)

    box((2 * RX, 2 * RY, t), (0, 0, -t / 2))                # floor, top face at z=0
    box((t, 2 * RY, RZ), (-RX - t / 2, 0, RZ / 2))          # x- wall
    box((t, 2 * RY, RZ), (RX + t / 2, 0, RZ / 2))           # x+ wall
    box((2 * RX + 2 * t, t, RZ), (0, -RY - t / 2, RZ / 2))  # y- wall
    box((2 * RX + 2 * t, t, RZ), (0, RY + t / 2, RZ / 2))   # y+ wall
    mesh = trimesh.util.concatenate(boxes)
    path.write_bytes(mesh.export(file_type="glb"))


def build_frames(rng, frames_dir):
    import cv2
    from PIL import Image, ImageDraw

    frames_dir.mkdir(parents=True, exist_ok=True)
    W, H = 480, 360
    lap_vars = []
    for i in range(45):
        ph = 2 * math.pi * i / 45  # fake orbit phase
        dx = int(round(30 * math.sin(ph)))
        dy = int(round(8 * math.cos(ph)))

        img = Image.new("RGB", (W, H), (168, 172, 178))  # wall
        d = ImageDraw.Draw(img)
        horizon = 160 + dy
        d.rectangle([0, horizon, W, H], fill=(120, 108, 96))       # floor
        d.line([0, horizon, W, horizon], fill=(70, 70, 70), width=2)
        # table
        d.rectangle([150 + dx, 210 + dy, 360 + dx, 300 + dy], fill=(110, 75, 45))
        # monitor
        d.rectangle([210 + dx, 130 + dy, 310 + dx, 205 + dy], fill=(25, 25, 30))
        # mug
        d.rectangle([170 + dx, 190 + dy, 190 + dx, 212 + dy], fill=(190, 45, 40))
        # book
        d.rectangle([310 + dx, 215 + dy, 350 + dx, 232 + dy], fill=(45, 90, 160))

        arr = np.asarray(img, dtype=np.float64)
        arr += rng.uniform(-25.0, 25.0, arr.shape)  # high-frequency texture
        arr = np.clip(arr, 0, 255).astype(np.uint8)

        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        lv = cv2.Laplacian(gray, cv2.CV_64F).var()
        assert lv >= 200, f"frame {i} too blurry: Laplacian var {lv:.1f}"
        lap_vars.append(lv)

        Image.fromarray(arr).save(frames_dir / f"{i:05d}.png")
    return lap_vars


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    rng = np.random.Generator(np.random.PCG64(SEED))
    OUT.mkdir(parents=True, exist_ok=True)

    # 1. gaussians.ply
    cloud, ranges = build_cloud(rng)
    ply_path = OUT / "gaussians.ply"
    save_ply(cloud, ply_path)

    # 2. clusters.json
    objects = [
        ("obj_000_table", "table", "table", False),
        ("obj_001_monitor", "monitor", "monitor", False),
        ("obj_002_mug", "mug", "mug", True),
        ("obj_003_book", "book", "book", True),
    ]
    clusters = {}
    for key, part, label, movable in objects:
        start, stop = ranges[part]
        clusters[key] = {
            "label": label,
            "indices": list(range(start, stop)),
            "views": 8,
            "views_total": 8,
            "vote_ratio": 0.95,
            "movable": movable,
        }
    clusters_path = OUT / "clusters.json"
    clusters_path.write_text(json.dumps(clusters, indent=2, sort_keys=True) + "\n")

    # verify indices: in range and disjoint across objects
    n = len(cloud)
    seen = set()
    for key, obj in clusters.items():
        idx = obj["indices"]
        assert all(0 <= i < n for i in idx), f"{key}: index out of range"
        s = set(idx)
        assert not (s & seen), f"{key}: overlapping indices"
        seen |= s

    # 3. lidar.glb
    glb_path = OUT / "lidar.glb"
    build_lidar_glb(glb_path)

    # 4. frames/
    lap_vars = build_frames(rng, OUT / "frames")

    # 5. fixture.json
    fixture_path = OUT / "fixture.json"
    fixture_path.write_text(json.dumps({
        "stub_psnr": 31.5,
        "stub_ssim": 0.93,
        "description": ("Synthetic tiny_room fixture: 3.0x2.4 m room, 2.3 m walls, "
                        "z-up, floor at z=0; table + monitor + mug + book; "
                        "box-room lidar mesh in the same frame; 45 sharp fake-orbit "
                        "frames at 480x360."),
        "seed": SEED,
    }, indent=2, sort_keys=True) + "\n")

    # summary
    print(f"gaussians: {n}  extents min={cloud.means.min(0)} max={cloud.means.max(0)}")
    for name, (a, b) in ranges.items():
        print(f"  {name:<10} rows [{a}, {b})")
    print(f"frame Laplacian var: min={min(lap_vars):.0f} "
          f"p10={float(np.percentile(lap_vars, 10)):.0f} max={max(lap_vars):.0f}")
    print()
    print(f"{'file':<28}{'bytes':>10}  sha256[:12]")
    files = [ply_path, clusters_path, glb_path, fixture_path]
    files += sorted((OUT / "frames").glob("*.png"))[:2]  # first two frames as spot checks
    for p in files:
        print(f"{p.relative_to(ROOT).as_posix():<28}{p.stat().st_size:>10}  {sha256(p)[:12]}")
    n_frames = len(list((OUT / 'frames').glob('*.png')))
    print(f"frames/: {n_frames} PNGs")


if __name__ == "__main__":
    main()
