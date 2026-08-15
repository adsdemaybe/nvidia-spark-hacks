"""Scripted routines that drive the physics twin (STRUCT_2.md 43).

Honest about what these are: the *waypoints* are fixed. Nothing here plans and
nothing here is a learned policy. What is real is everything downstream of the
waypoints -- IK has to actually find a solution, the actuators have to actually
get there, and whether the cube ends up in the bin is decided by contact and
gravity rather than by the script declaring victory.

This is a stand-in for a retargeted human demonstration, which is what REPLAY
will drive once TEACH episodes flow through STRUCT_2.md 13D.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .ik import solve_ik
from .scene_mjcf import BIN_POS, CUBE_REST, TABLE_SIZE

if TYPE_CHECKING:
    from .twin import MujocoTwinSource

Vec3 = tuple[float, float, float]

CUBE_TOP = TABLE_SIZE[2] + 0.03


@dataclass(frozen=True)
class Waypoint:
    """Hold `seconds` with the tool driven toward `position_m`.

    `grasp` is tri-state on purpose: None means "whatever the last waypoint
    decided", so only the waypoints that change the gripper mention it.
    """

    seconds: float
    position_m: Vec3
    grasp: bool | None = None


GRASP_HOVER = 0.06
APPROACH_CLEARANCE = 0.16
CARRY_HEIGHT = TABLE_SIZE[2] + 0.32
RELEASE_HEIGHT = 0.34


class PickAndPlaceDirector:
    """Reach the cube, lift it, carry it over the bin, let go."""

    def __init__(self, waypoints: tuple[Waypoint, ...] | None = None) -> None:
        # Hover just above the cube rather than descending onto its centre. The
        # tool is a solid body: driving the site to the cube's centre pushes the
        # cube aside first, and the weld then preserves that offset all the way
        # to the bin, so the drop misses.
        cube: Vec3 = (CUBE_REST[0], CUBE_REST[1], CUBE_TOP + GRASP_HOVER)
        above_cube: Vec3 = (cube[0], cube[1], cube[2] + APPROACH_CLEARANCE)
        carry: Vec3 = (cube[0], cube[1], CARRY_HEIGHT)
        over_bin: Vec3 = (BIN_POS[0], BIN_POS[1], CARRY_HEIGHT)
        drop: Vec3 = (BIN_POS[0], BIN_POS[1], RELEASE_HEIGHT)

        self.waypoints = waypoints or (
            Waypoint(0.8, above_cube, grasp=False),
            Waypoint(1.0, cube),
            Waypoint(0.4, cube, grasp=True),
            Waypoint(0.8, carry),
            Waypoint(1.4, over_bin),
            Waypoint(0.8, drop),
            Waypoint(0.3, drop, grasp=False),
            Waypoint(1.5, over_bin),
        )
        self.duration_s = sum(w.seconds for w in self.waypoints)

    def waypoint_at(self, seconds: float) -> Waypoint:
        t = seconds % self.duration_s
        for waypoint in self.waypoints:
            if t < waypoint.seconds:
                return waypoint
            t -= waypoint.seconds
        return self.waypoints[-1]

    def grasp_at(self, seconds: float) -> bool:
        """The gripper state in force at `seconds`, carried forward from the
        last waypoint that set it."""
        t = seconds % self.duration_s
        state = False
        for waypoint in self.waypoints:
            if waypoint.grasp is not None:
                state = waypoint.grasp
            if t < waypoint.seconds:
                return state
            t -= waypoint.seconds
        return state

    def drive(self, sim: MujocoTwinSource, seconds: float) -> None:
        """Command the arm toward the active waypoint and set the gripper."""
        waypoint = self.waypoint_at(seconds)
        result = solve_ik(sim, waypoint.position_m)
        sim.command(result.joint_positions)

        wants_grasp = self.grasp_at(seconds)
        if wants_grasp and not sim.holding:
            sim.grasp()
        elif not wants_grasp and sim.holding:
            sim.release()
