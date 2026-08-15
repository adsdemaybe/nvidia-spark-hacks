"""End-to-end physics gate: does the arm actually get the cube into the bin?

This is the test that makes the local twin worth having. Everything else checks
a piece; this one runs the whole routine and asks the solver, not the script,
whether the task succeeded (STRUCT_2.md 63 -- a task predicate, evaluated, not
asserted).
"""
from __future__ import annotations

from arxr.sim.director import PickAndPlaceDirector
from arxr.sim.scene_mjcf import BIN_POS, BIN_SIZE
from arxr.sim.twin import MujocoTwinSource

HZ = 60.0


def run_routine(sim: MujocoTwinSource, director: PickAndPlaceDirector) -> None:
    for tick in range(int(HZ * director.duration_s)):
        director.drive(sim, tick / HZ)
        sim.step()


def test_the_cube_ends_up_in_the_bin():
    sim = MujocoTwinSource(control_hz=HZ)
    director = PickAndPlaceDirector()

    run_routine(sim, director)

    x, y, z = sim.cube_position()
    assert abs(x - BIN_POS[0]) < BIN_SIZE[0] / 2, f"cube x={x:.3f} outside the bin"
    assert abs(y - BIN_POS[1]) < BIN_SIZE[1] / 2, f"cube y={y:.3f} outside the bin"
    assert z < BIN_SIZE[2], f"cube z={z:.3f} is above the bin rim"


def test_the_task_predicate_reports_success_from_geometry():
    sim = MujocoTwinSource(control_hz=HZ)

    run_routine(sim, PickAndPlaceDirector())

    assert sim.state().task is not None
    assert sim.state().task.status == "success"


def test_the_cube_is_carried_rather_than_teleported():
    """Between grasp and release the cube must stay near the tool. A weld that
    snapped it into place would pass the in-the-bin test while proving nothing."""
    sim = MujocoTwinSource(control_hz=HZ)
    director = PickAndPlaceDirector()

    worst = 0.0
    for tick in range(int(HZ * director.duration_s)):
        director.drive(sim, tick / HZ)
        sim.step()
        if sim.holding:
            tool = sim.end_effector_position()
            cube = sim.cube_position()
            gap = sum((a - b) ** 2 for a, b in zip(tool, cube, strict=True)) ** 0.5
            worst = max(worst, gap)

    assert 0.0 < worst < 0.20, f"cube drifted {worst:.3f} m from the tool while held"


def test_the_routine_is_deterministic():
    a, b = MujocoTwinSource(control_hz=HZ), MujocoTwinSource(control_hz=HZ)
    run_routine(a, PickAndPlaceDirector())
    run_routine(b, PickAndPlaceDirector())

    assert a.cube_position() == b.cube_position()
