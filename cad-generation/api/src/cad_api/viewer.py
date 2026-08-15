"""Viser viewer — §1's "3D viewer: Viser (interactive joint view)".

    python -m cad_api.viewer out/rover/robot.ir.json

Serves an interactive scene on http://127.0.0.1:8080: every link as a placed
mesh, one slider per actuated joint, and a toggle that draws each joint's axis.

Nothing here knows what a rover is. Links, joints, sliders and axes all come from
whatever `RobotIR` is loaded, so this is the viewer for every robot the platform
designs, not a rover viewer (§2: "topology is data, not code").

Why Viser and not a hand-rolled renderer: §1 resolves the 3D viewer to Viser plus
`<model-viewer>` off the shelf, and shrinks custom Three.js to the coverage-matrix
overlay only. A bespoke renderer is a second thing to maintain that shows less.

Articulation is the point. A static assembly answers "what does it look like",
which STEP already answers. Dragging a joint through its range answers "does this
joint do what the design says it does" — the question that catches a wheel whose
geometry axis and joint axis disagree, which no static view makes visible.

This module performs I/O and serves a socket, so it lives in `cad_api`, never in
`engine` (§11 non-negotiable #7: the engine has zero I/O).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from engine.geometry.registry import build as build_geometry
from engine.ir import Joint, RobotIR
from engine.kinematics import (
    _axis_angle_matrix,
    joint_world_frame,
    link_frames,
    link_geometry_transform,
    subtree_links,
)

_M_TO_MM = 1000.0

# Distinct hues in registration order. Per-link colour is what makes a detached
# link obvious: two solids that never touch read as one object in a single colour.
_PALETTE = [
    (0x8C, 0x94, 0x9B), (0x4E, 0x7C, 0xA1), (0xC1, 0x7D, 0x3A), (0x6B, 0x9E, 0x78),
    (0xA1, 0x6B, 0x9E), (0xB5, 0x5F, 0x5F), (0x7A, 0x8C, 0x5A), (0x5F, 0x6B, 0xB5),
]


def _mesh(part) -> tuple[np.ndarray, np.ndarray]:
    """Tessellate a build123d Part into (vertices_mm, triangles).

    `Shape.tessellate` is the fast path and is all a primitive from the geometry
    registry ever needs. It is not enough for imported STEP: OCCT returns a null
    `Poly_Triangulation` for faces it cannot mesh at the requested tolerance, and
    build123d walks into it — `AttributeError: 'NoneType' has no attribute
    'NbNodes'`, which killed the viewer outright on the SO-101 arm while the
    STEP/STL export of the same part succeeded.

    So fall back to the exporter, which handles those faces, and read the result
    back. Slower and it touches the disk, which is why it is not the default, but
    a viewer that renders every robot slowly beats one that renders most robots
    quickly and crashes on the rest.
    """
    try:
        verts, tris = part.tessellate(tolerance=0.35, angular_tolerance=0.3)
        return (
            np.array([[v.X, v.Y, v.Z] for v in verts], dtype=np.float32),
            np.array(tris, dtype=np.uint32),
        )
    except (AttributeError, RuntimeError):
        import tempfile

        import trimesh
        from build123d import export_stl

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "link.stl"
            export_stl(part, str(path), tolerance=0.35, angular_tolerance=0.3)
            mesh = trimesh.load(str(path))
        return (
            np.asarray(mesh.vertices, dtype=np.float32),
            np.asarray(mesh.faces, dtype=np.uint32),
        )


def _quat_wxyz(rot: np.ndarray) -> np.ndarray:
    """Rotation matrix -> (w, x, y, z), Viser's orientation convention.

    Shepperd's method: pick the branch off the largest diagonal term rather than
    always taking the trace branch, which loses all precision as the trace
    approaches -1 (a 180-degree joint, i.e. exactly the configuration a slider is
    most likely to be dragged to).
    """
    m, t = rot, float(np.trace(rot))
    if t > 0.0:
        s = np.sqrt(t + 1.0) * 2.0
        return np.array([0.25 * s, (m[2, 1] - m[1, 2]) / s,
                         (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s])
    i = int(np.argmax(np.diagonal(m)))
    j, k = (i + 1) % 3, (i + 2) % 3
    s = np.sqrt(1.0 + m[i, i] - m[j, j] - m[k, k]) * 2.0
    q = np.empty(4)
    q[0] = (m[k, j] - m[j, k]) / s
    q[1 + i] = 0.25 * s
    q[1 + j] = (m[j, i] + m[i, j]) / s
    q[1 + k] = (m[k, i] + m[i, k]) / s
    return q


def _topological_joints(ir: RobotIR) -> list[Joint]:
    """Joints ordered parents-before-children.

    Articulation composes each joint's rotation onto frames its ancestors have
    already moved. Out of order, a child joint's pivot is read from a stale parent
    frame and the subtree swings from the wrong point — visible only on multi-level
    topologies (an arm, a leg), never on this rover's one-level tree, which is
    exactly how it would ship unnoticed.
    """
    depth: dict[str, int] = {ir.root_link: 0}
    remaining = list(ir.joints)
    ordered: list[Joint] = []
    while remaining:
        progressed = False
        for joint in list(remaining):
            if joint.parent in depth:
                depth[joint.child] = depth[joint.parent] + 1
                ordered.append(joint)
                remaining.remove(joint)
                progressed = True
        if not progressed:  # unreachable subtree — RobotIR validation allows it
            ordered.extend(remaining)
            break
    return ordered


def articulated_frames(ir: RobotIR, angles: dict[str, float]) -> dict[str, np.ndarray]:
    """Link world frames with each joint driven to `angles[joint_id]`.

    Revolute angles are radians, prismatic are metres. `link_frames` gives the
    home configuration only; this rotates each joint's whole subtree about the
    joint's world pivot, which is what a slider needs.
    """
    frames = link_frames(ir)
    for joint in _topological_joints(ir):
        value = float(angles.get(joint.id, 0.0))
        if value == 0.0 or joint.kind == "fixed":
            continue

        pivot = joint_world_frame(ir, joint, frames)
        axis = pivot[:3, :3] @ np.array(joint.axis.as_tuple(), dtype=float)
        norm = float(np.linalg.norm(axis))
        if norm < 1e-12:
            continue
        axis /= norm

        delta = np.eye(4)
        if joint.kind == "revolute":
            origin = pivot[:3, 3]
            delta[:3, :3] = _axis_angle_matrix(axis, value)
            delta[:3, 3] = origin - delta[:3, :3] @ origin
        else:  # prismatic
            delta[:3, 3] = axis * value

        for link_id in subtree_links(ir, joint.child):
            frames[link_id] = delta @ frames[link_id]
    return frames


def serve(ir: RobotIR, *, host: str = "127.0.0.1", port: int = 8080, block: bool = True):
    import viser

    server = viser.ViserServer(host=host, port=port)
    server.scene.world_axes.visible = False

    meshes = {link.id: _mesh(build_geometry(link.geometry).part) for link in ir.links}

    handles = {}
    for index, link in enumerate(ir.links):
        vertices, faces = meshes[link.id]
        handles[link.id] = server.scene.add_mesh_simple(
            f"/robot/{link.id}",
            vertices=vertices,
            faces=faces,
            color=_PALETTE[index % len(_PALETTE)],
            flat_shading=False,
        )

    # The plane the robot is supposed to stand on, sized to the design. Without
    # it a link floating above the ground looks identical to one resting on it.
    # Scene units are millimetres — the meshes come out of build123d in mm and
    # only the IR is SI, so a grid sized in metres would be a 1 mm speck.
    home = link_frames(ir)
    extent = 200.0
    for link in ir.links:
        vertices, _ = meshes[link.id]
        if not len(vertices):
            continue
        transform = link_geometry_transform(ir, link.id, home)
        world = vertices @ transform[:3, :3].T + transform[:3, 3] * _M_TO_MM
        extent = max(extent, float(np.abs(world).max()))
    span = max(400.0, extent * 2.4)
    server.scene.add_grid(
        "/ground", width=span, height=span, cell_size=50.0, plane="xy",
        position=(0.0, 0.0, 0.0),
    )

    axis_handles: list = []
    angles: dict[str, float] = {joint.id: 0.0 for joint in ir.joints}

    def refresh() -> None:
        frames = articulated_frames(ir, angles)
        for link in ir.links:
            transform = link_geometry_transform(ir, link.id, frames)
            handle = handles[link.id]
            handle.position = tuple(transform[:3, 3] * _M_TO_MM)
            handle.wxyz = _quat_wxyz(transform[:3, :3])
        for handle in axis_handles:
            handle.remove()
        axis_handles.clear()
        if show_axes.value:
            for joint in ir.joints:
                if joint.kind == "fixed":
                    continue
                pivot = joint_world_frame(ir, joint, frames)
                axis = pivot[:3, :3] @ np.array(joint.axis.as_tuple(), dtype=float)
                norm = float(np.linalg.norm(axis))
                if norm < 1e-12:
                    continue
                origin = pivot[:3, 3] * _M_TO_MM
                half = (axis / norm) * (span * 0.12)
                axis_handles.append(
                    server.scene.add_line_segments(
                        f"/axes/{joint.id}",
                        points=np.array([[origin - half, origin + half]]),
                        colors=(0xE0, 0x73, 0x6D),
                        line_width=3.0,
                    )
                )

    with server.gui.add_folder("Design"):
        server.gui.add_text("name", initial_value=ir.name, disabled=True)
        server.gui.add_text(
            "topology",
            initial_value=f"{len(ir.links)} links, {len(ir.joints)} joints",
            disabled=True,
        )

    show_axes = server.gui.add_checkbox("Joint axes", initial_value=False)
    show_axes.on_update(lambda _: refresh())

    movable = [j for j in ir.joints if j.kind != "fixed"]
    sliders = {}
    if movable:
        with server.gui.add_folder(f"Joints ({len(movable)})"):
            for joint in movable:
                prismatic = joint.kind == "prismatic"
                slider = server.gui.add_slider(
                    joint.id,
                    min=-0.2 if prismatic else -np.pi,
                    max=0.2 if prismatic else np.pi,
                    step=0.005 if prismatic else 0.01,
                    initial_value=0.0,
                )
                sliders[joint.id] = slider

                def _on_update(_, joint_id=joint.id, slider=slider) -> None:
                    angles[joint_id] = float(slider.value)
                    refresh()

                slider.on_update(_on_update)

            reset = server.gui.add_button("Home configuration")

            @reset.on_click
            def _(_) -> None:
                for joint_id, slider in sliders.items():
                    angles[joint_id] = 0.0
                    slider.value = 0.0
                refresh()

    refresh()
    print(f"viser: http://{host}:{port}  —  {ir.name}: "
          f"{len(ir.links)} links, {len(movable)} movable joint(s)")
    if block:
        try:
            import time

            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\nstopped")
    return server


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m cad_api.viewer",
        description="Serve a RobotIR as an interactive Viser scene.",
    )
    parser.add_argument("ir_path", help="path to a RobotIR JSON file")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)

    ir = RobotIR.model_validate(json.loads(Path(args.ir_path).read_text(encoding="utf-8")))
    serve(ir, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
