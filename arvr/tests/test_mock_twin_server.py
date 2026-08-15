"""spec section 60 acceptance test: mock TwinState streaming.

Exercises the mock server's message-generation function directly (no actual
socket) so this stays fast and doesn't need a free port in CI.
"""

from __future__ import annotations

from ar_contracts import TwinState
from mock_twin_server import synthetic_state


def test_synthetic_state_is_schema_valid_twin_state():
    state = synthetic_state("demo_room", t=0.0)
    assert isinstance(state, TwinState)
    assert state.scene_id == "demo_room"
    assert len(state.robot.joint_positions) == 6


def test_synthetic_state_evolves_over_time():
    a = synthetic_state("demo_room", t=0.0)
    b = synthetic_state("demo_room", t=1.0)
    assert a.robot.joint_positions != b.robot.joint_positions
