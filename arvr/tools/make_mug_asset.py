#!/usr/bin/env python3
"""Generate the fixture mug InteractableAsset deterministically.

    uv run --no-sync python tools/make_mug_asset.py

Writes fixtures/spatial-training/assets/mug/{manifest,interaction}.json and
asset.glb, the same bundle shape as tools/make_button_asset.py so
`spatial_providers.FixtureAssetProvider` and `ar_backend.assets` read it with
no change.

Why generate rather than download: the network is sandboxed and a third-party
mug model would arrive with a licence this repo would then have to ship and
audit (see fixtures/robot/so101_real/NOTICE.md for what that costs). Procedural
geometry has no such question, and the shape a demonstrator needs is simple.

Why a mug and not the procedural cylinder it replaces: the object in the
can-pickup scene is the thing a human grasps on camera, and the grasp is the
recorded signal. A featureless cylinder affords exactly one grasp — a wrap
around the barrel. A mug affords two, because it has a handle, and a handle is
also what makes the object read as a mug at a glance rather than as "some
cylinder". Both grasps are declared in interaction.json so a task can name
which one a demonstration used.

Not a reconstruction — same "correct units and plausible extents" bar as
tools/make_assets.py. Deterministic: no clock, no RNG, no boolean engine, so
regenerating produces byte-identical files and a fixture diff always means
somebody changed something on purpose.

Frame: struct_world convention — right-handed, Z-up, meters. The mug stands
upright in its own local frame with its base plane at exactly z = 0 and its
axis of revolution on local (x=0, y=0), so a client places it by setting the
object's origin to the point on the table where the base should rest. Verified
by tests/test_mug_asset.py, which asserts the exported bounds directly rather
than trusting this comment.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import trimesh

ARVR_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_DIR = ARVR_ROOT / "fixtures" / "spatial-training" / "assets" / "mug"

ASSET_ID = "mug_01"

# A standard 12 oz / 355 ml ceramic coffee mug: 89 mm tall, 82 mm across the
# outside, 4 mm walls. Those are the measurements of the ubiquitous stoneware
# diner mug, and they are real numbers rather than round ones for the same
# reason canPickupLayout.ts states the can as 66 x 123 mm: the grasp aperture a
# human uses is set by the actual object. A mug 1 cm wider is a different
# grasp, and the recorded finger poses would teach a different one.
#
# The wall and floor thicknesses give an interior of radius 37 mm and depth
# 83 mm, i.e. pi * 0.037^2 * 0.083 = 357 ml brim-full, which is what a mug sold
# as 12 oz actually holds to the rim.
MUG_HEIGHT_M = 0.089
MUG_OUTER_RADIUS_M = 0.041
WALL_THICKNESS_M = 0.004
FLOOR_THICKNESS_M = 0.006
MUG_INNER_RADIUS_M = MUG_OUTER_RADIUS_M - WALL_THICKNESS_M

# Handle. A ceramic mug handle is a round-ish bar roughly 9 mm thick bowed out
# from the wall, and the opening it leaves has to take two fingers: here the
# gap is 22.5 mm deep (from the outer wall to the inside of the bar at the
# apex) and 29 mm tall, which is a normal two-finger hook.
#
# The bar's centreline is a circular arc in the local XZ plane, so the handle
# is on +X and the mug's widest point is the handle side. Its ends stop at
# x = 39 mm, i.e. inside the 37..41 mm wall, so the tube's end caps are buried
# in the ceramic and the join reads as solid without any boolean union.
HANDLE_TUBE_RADIUS_M = 0.0045
HANDLE_ATTACH_X_M = 0.039
HANDLE_ATTACH_Z_LOW_M = 0.026
HANDLE_ATTACH_Z_HIGH_M = 0.064
HANDLE_APEX_X_M = 0.068

# Tessellation. Sized for a real-time renderer looking at an 8 cm object from
# arm's length: 64 sections around the body is smooth to the eye at that size,
# and the whole mug lands at ~2k triangles, which is nothing next to the camera
# feed and hand-tracking work the client is already doing every frame.
BODY_SECTIONS = 64
HANDLE_PATH_SAMPLES = 48
HANDLE_RING_SAMPLES = 16


def mug_body() -> trimesh.Trimesh:
    """The hollow vessel, as a solid of revolution.

    The cross-section is a closed profile in (radius, height): up the outside,
    across the rim, down the inside, across the floor, and back along the axis
    to the start. Revolving that closed profile gives a single watertight shell
    with a real cavity in it — no boolean subtraction, which is what
    tools/make_assets.py avoids too, because a CSG union would pull in
    manifold3d or blender as a build dependency and the result would vary with
    whichever engine happened to be installed.
    """
    profile = np.array(
        [
            [0.0, 0.0],  # on the axis, at the base plane
            [MUG_OUTER_RADIUS_M, 0.0],  # outward across the foot
            [MUG_OUTER_RADIUS_M, MUG_HEIGHT_M],  # up the outside wall
            [MUG_INNER_RADIUS_M, MUG_HEIGHT_M],  # across the rim
            [MUG_INNER_RADIUS_M, FLOOR_THICKNESS_M],  # down the inside wall
            [0.0, FLOOR_THICKNESS_M],  # inward across the cavity floor
        ]
    )
    return trimesh.creation.revolve(profile, sections=BODY_SECTIONS)


def mug_handle() -> trimesh.Trimesh:
    """The handle bar: a circular cross-section swept along a circular arc.

    Swept by hand rather than with trimesh.creation.sweep_polygon because the
    arc lies in one plane, which makes the sweep frame trivial (the ring's two
    basis vectors are the in-plane normal and world Y) and keeps the vertex
    order a pure function of the constants above.
    """
    z_mid = (HANDLE_ATTACH_Z_LOW_M + HANDLE_ATTACH_Z_HIGH_M) / 2.0
    half_span = (HANDLE_ATTACH_Z_HIGH_M - HANDLE_ATTACH_Z_LOW_M) / 2.0

    # Centre and radius of the unique circle that passes through both attach
    # points and reaches HANDLE_APEX_X_M at z_mid. Solved here rather than
    # hard-coded so moving an attach point cannot silently leave a stale radius
    # behind, which would detach the handle from the wall.
    centre_x = (HANDLE_APEX_X_M**2 - HANDLE_ATTACH_X_M**2 - half_span**2) / (
        2.0 * (HANDLE_APEX_X_M - HANDLE_ATTACH_X_M)
    )
    radius = HANDLE_APEX_X_M - centre_x

    # Sweep from the lower attach point, out around the apex, to the upper one.
    start_angle = np.arctan2(-half_span, HANDLE_ATTACH_X_M - centre_x)
    angles = np.linspace(start_angle, -start_angle, HANDLE_PATH_SAMPLES)
    path = np.column_stack(
        [
            centre_x + radius * np.cos(angles),
            np.zeros(HANDLE_PATH_SAMPLES),
            z_mid + radius * np.sin(angles),
        ]
    )

    tangent = np.gradient(path, axis=0)
    tangent /= np.linalg.norm(tangent, axis=1)[:, None]
    y_axis = np.array([0.0, 1.0, 0.0])
    in_plane = np.cross(tangent, y_axis)
    in_plane /= np.linalg.norm(in_plane, axis=1)[:, None]

    phase = np.linspace(0.0, 2.0 * np.pi, HANDLE_RING_SAMPLES, endpoint=False)
    ring = (
        np.cos(phase)[None, :, None] * in_plane[:, None, :]
        + np.sin(phase)[None, :, None] * y_axis[None, None, :]
    )
    vertices = (path[:, None, :] + HANDLE_TUBE_RADIUS_M * ring).reshape(-1, 3)

    faces: list[tuple[int, int, int]] = []
    ring_n = HANDLE_RING_SAMPLES
    for i in range(HANDLE_PATH_SAMPLES - 1):
        for j in range(ring_n):
            a = i * ring_n + j
            b = i * ring_n + (j + 1) % ring_n
            c = (i + 1) * ring_n + j
            d = (i + 1) * ring_n + (j + 1) % ring_n
            # Wound so the face normals point out of the tube; trimesh reports
            # a negative volume for the other winding, which is how this was
            # checked rather than by eye.
            faces.append((a, d, b))
            faces.append((a, c, d))

    # Flat caps on both ends. They end up inside the mug wall, but a closed
    # shell is still worth having: an open tube would make the whole export
    # non-watertight and the test could then no longer tell a modelling mistake
    # from an expected hole.
    first_centre = len(vertices)
    last_centre = first_centre + 1
    vertices = np.vstack([vertices, path[0], path[-1]])
    last_ring = (HANDLE_PATH_SAMPLES - 1) * ring_n
    for j in range(ring_n):
        faces.append((first_centre, j, (j + 1) % ring_n))
        faces.append((last_centre, last_ring + (j + 1) % ring_n, last_ring + j))

    return trimesh.Trimesh(vertices=vertices, faces=np.array(faces), process=False)


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n")
    print(f"wrote {path.relative_to(ARVR_ROOT)}")


def make_manifest() -> None:
    write_json(
        BUNDLE_DIR / "manifest.json",
        {
            "schema_version": "1.0",
            "asset_id": ASSET_ID,
            "asset_glb": "asset.glb",
            "interaction": "interaction.json",
        },
    )


def make_interaction() -> None:
    """Both grasps a demonstrator can actually perform, as InteractableAsset parts.

    `local_origin_m` is the point the grasp closes around and `axis` is the
    direction the hand or gripper travels while approaching it — the same
    "direction of the interaction motion" meaning `axis` carries for the
    button's press. The two approaches are opposed because the handle is on +X:
    you reach past the handle for the barrel, and back toward the mug for the
    handle itself.
    """
    write_json(
        BUNDLE_DIR / "interaction.json",
        {
            "schema_version": "1.0",
            "asset_id": ASSET_ID,
            "parts": {
                "body": {
                    "interaction": "grasp",
                    "local_origin_m": [0.0, 0.0, MUG_HEIGHT_M / 2.0],
                    "axis": [1.0, 0.0, 0.0],
                },
                "handle": {
                    "interaction": "grasp",
                    "local_origin_m": [
                        HANDLE_APEX_X_M,
                        0.0,
                        (HANDLE_ATTACH_Z_LOW_M + HANDLE_ATTACH_Z_HIGH_M) / 2.0,
                    ],
                    "axis": [-1.0, 0.0, 0.0],
                },
            },
        },
    )


def make_glb() -> None:
    """Export body and handle as two named nodes, not one merged mesh.

    The names match interaction.json's part names, so a client that wants to
    highlight the part a task refers to can find it by name instead of
    guessing from geometry.
    """
    scene = trimesh.Scene({"body": mug_body(), "handle": mug_handle()})
    glb = trimesh.exchange.gltf.export_glb(scene, include_normals=True)
    path = BUNDLE_DIR / "asset.glb"
    path.write_bytes(glb)
    print(f"wrote {path.relative_to(ARVR_ROOT)} ({path.stat().st_size} bytes)")


def main() -> None:
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    make_manifest()
    make_interaction()
    make_glb()


if __name__ == "__main__":
    main()
