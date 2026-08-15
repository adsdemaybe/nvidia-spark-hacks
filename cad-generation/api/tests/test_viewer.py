import numpy as np
import pytest

from cad_api.viewer import _quat_wxyz, _topological_joints, articulated_frames
from engine.examples import simple_rover
from engine.kinematics import link_frames, subtree_links


def test_home_configuration_matches_link_frames():
    """Zero on every slider must reproduce exactly what evaluate() scored. A
    viewer that drifts from the harness's frames shows a robot nobody
    evaluated."""
    ir = simple_rover()
    home = articulated_frames(ir, {})
    for link_id, frame in link_frames(ir).items():
        assert np.allclose(home[link_id], frame)


def test_driving_a_joint_moves_only_its_subtree():
    ir = simple_rover()
    joint = next(j for j in ir.joints if j.kind == "revolute")
    moved_ids = set(subtree_links(ir, joint.child))

    home = articulated_frames(ir, {})
    posed = articulated_frames(ir, {joint.id: 0.7})

    for link in ir.links:
        same = np.allclose(home[link.id], posed[link.id])
        assert same is (link.id not in moved_ids), link.id


def test_revolute_rotation_preserves_the_pivot_distance():
    """A revolute joint spins its subtree about the pivot — it must not
    translate it. Getting the conjugation wrong (rotating about the world origin
    instead of the joint) still looks plausible on screen for a joint near the
    origin, and flings the part away for one that isn't."""
    ir = simple_rover()
    joint = next(j for j in ir.joints if j.kind == "revolute")

    from engine.kinematics import joint_world_frame

    pivot = joint_world_frame(ir, joint, link_frames(ir))[:3, 3]
    home = articulated_frames(ir, {})[joint.child][:3, 3]
    for angle in (0.3, 1.2, np.pi):
        posed = articulated_frames(ir, {joint.id: angle})[joint.child][:3, 3]
        assert np.linalg.norm(posed - pivot) == pytest.approx(
            np.linalg.norm(home - pivot), abs=1e-9
        )


def test_full_turn_returns_to_home():
    ir = simple_rover()
    joint = next(j for j in ir.joints if j.kind == "revolute")
    home = articulated_frames(ir, {})
    full = articulated_frames(ir, {joint.id: 2 * np.pi})
    assert np.allclose(home[joint.child], full[joint.child], atol=1e-9)


def test_joints_are_ordered_parents_before_children():
    ir = simple_rover()
    seen = {ir.root_link}
    for joint in _topological_joints(ir):
        assert joint.parent in seen, f"{joint.id} ordered before its parent"
        seen.add(joint.child)


@pytest.mark.parametrize(
    "matrix",
    [
        np.eye(3),
        np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]),  # +90 deg Z
        np.diag([1.0, -1.0, -1.0]),  # 180 deg X — trace = -1, the degenerate branch
        np.diag([-1.0, 1.0, -1.0]),  # 180 deg Y
        np.diag([-1.0, -1.0, 1.0]),  # 180 deg Z
    ],
)
def test_quaternion_round_trips_through_the_rotation(matrix):
    """The trace branch loses all precision as trace -> -1, which is exactly a
    half-turn — the configuration a slider reaches most often."""
    w, x, y, z = _quat_wxyz(matrix)
    assert np.hypot(np.hypot(w, x), np.hypot(y, z)) == pytest.approx(1.0)
    rebuilt = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    assert np.allclose(rebuilt, matrix, atol=1e-9)
