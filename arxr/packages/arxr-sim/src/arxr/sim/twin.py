"""A TwinState source backed by real rigid-body physics (STRUCT_2.md 43).

Same contract, same route, same client as MockTwinSource -- what changes is
that the numbers come out of a solver instead of a sine. Objects fall, land,
tip and settle because of gravity and contact.

This is NOT the authoritative twin. OpenUSD in Isaac Sim on the Spark is
(STRUCT_2.md 52, 53). What this is for: making the demo honest, and making
REPLAY mean something, on a machine that has neither.
"""
from __future__ import annotations

from collections.abc import Sequence

import mujoco
import numpy as np
from arxr.core.schemas import ObjectState, RobotState, TaskState, TaskStatus, TwinState
from arxr.core.twin_mock import EPOCH_NS

from .scene_mjcf import ARM_JOINTS, BIN_POS, BIN_SIZE, build_mjcf

DEFAULT_CONTROL_HZ = 30.0

# The cube counts as delivered once it is inside the bin's footprint and below
# its rim. Deterministic geometry, not a judgement call (STRUCT_2.md 63).
BIN_INNER_XY = (BIN_SIZE[0] / 2 - BIN_SIZE[2] * 0.0, BIN_SIZE[1] / 2)


class MujocoTwinSource:
    def __init__(
        self,
        scene_id: str = "demo_room",
        control_hz: float = DEFAULT_CONTROL_HZ,
        cube_height_m: float | None = None,
        epoch_ns: int = EPOCH_NS,
    ) -> None:
        if control_hz <= 0.0:
            raise ValueError(f"control_hz must be positive; got {control_hz}")

        self.scene_id = scene_id
        self.control_hz = control_hz
        self.epoch_ns = epoch_ns

        self._model = mujoco.MjModel.from_xml_string(build_mjcf(cube_height_m))
        self._data = mujoco.MjData(self._model)
        self._steps_per_tick = max(1, round((1.0 / control_hz) / self._model.opt.timestep))
        self._tick = 0

        self._joint_ids = [
            mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, name)
            for name in ARM_JOINTS
        ]
        self._cube_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_BODY, "cube_01")
        self._site_id = mujoco.mj_name2id(
            self._model, mujoco.mjtObj.mjOBJ_SITE, "end_effector"
        )
        self._tool_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_BODY, "tool")
        self._grasp_id = mujoco.mj_name2id(
            self._model, mujoco.mjtObj.mjOBJ_EQUALITY, "grasp"
        )

        mujoco.mj_forward(self._model, self._data)

    # ------------------------------------------------------------- inspection --
    @property
    def joint_count(self) -> int:
        return len(self._joint_ids)

    @property
    def model(self):
        return self._model

    @property
    def site_id(self) -> int:
        return self._site_id

    def raw_qpos(self) -> np.ndarray:
        return np.array(self._data.qpos, copy=True)

    def joint_qpos_indices(self) -> tuple[int, ...]:
        return tuple(int(self._model.jnt_qposadr[jid]) for jid in self._joint_ids)

    def joint_dof_indices(self) -> tuple[int, ...]:
        return tuple(int(self._model.jnt_dofadr[jid]) for jid in self._joint_ids)

    def joint_limits(self) -> tuple[tuple[float, ...], tuple[float, ...]]:
        lower = tuple(float(self._model.jnt_range[jid][0]) for jid in self._joint_ids)
        upper = tuple(float(self._model.jnt_range[jid][1]) for jid in self._joint_ids)
        return lower, upper

    def set_joint_positions(self, values: Sequence[float]) -> None:
        """Teleport the arm. For solving and previewing, not for stepping --
        physics does not run between the old pose and the new one."""
        if len(values) != self.joint_count:
            raise ValueError(f"expected {self.joint_count} joints, got {len(values)}")
        for jid, value in zip(self._joint_ids, values, strict=True):
            self._data.qpos[self._model.jnt_qposadr[jid]] = float(value)
        mujoco.mj_forward(self._model, self._data)

    def cube_position(self) -> tuple[float, float, float]:
        return tuple(float(v) for v in self._data.xpos[self._cube_id])  # type: ignore[return-value]

    def end_effector_position(self) -> tuple[float, float, float]:
        """Where the tool actually is. Used by PLACE to draw the reach envelope
        and by the tests to prove the arm can get to the work."""
        return tuple(float(v) for v in self._data.site_xpos[self._site_id])  # type: ignore[return-value]

    def joint_positions(self) -> tuple[float, ...]:
        return tuple(
            float(self._data.qpos[self._model.jnt_qposadr[jid]]) for jid in self._joint_ids
        )

    # ---------------------------------------------------------------- control --
    def command(self, targets: Sequence[float]) -> None:
        """Set position-actuator targets, one per arm joint."""
        if len(targets) != self.joint_count:
            raise ValueError(
                f"expected {self.joint_count} joint targets, got {len(targets)}"
            )
        self._data.ctrl[: self.joint_count] = np.asarray(targets, dtype=float)

    # ------------------------------------------------------------------ grasp --
    def grasp(self) -> None:
        """Weld the cube to the tool at its current relative pose.

        Capturing the *current* offset matters: welding to a fixed offset would
        snap the cube into the gripper, which looks like a successful grasp in a
        video while proving nothing about whether the arm actually got there.
        """
        tool_pos = self._data.xpos[self._tool_id]
        tool_quat = self._data.xquat[self._tool_id]
        cube_pos = self._data.xpos[self._cube_id]
        cube_quat = self._data.xquat[self._cube_id]

        tool_quat_inv = np.zeros(4)
        mujoco.mju_negQuat(tool_quat_inv, tool_quat)

        rel_pos = np.zeros(3)
        mujoco.mju_rotVecQuat(rel_pos, cube_pos - tool_pos, tool_quat_inv)
        rel_quat = np.zeros(4)
        mujoco.mju_mulQuat(rel_quat, tool_quat_inv, cube_quat)

        # eq_data is model state, eq_active is per-step data state.
        self._model.eq_data[self._grasp_id, 0:3] = 0.0
        self._model.eq_data[self._grasp_id, 3:6] = rel_pos
        self._model.eq_data[self._grasp_id, 6:10] = rel_quat
        self._model.eq_data[self._grasp_id, 10] = 1.0
        self._data.eq_active[self._grasp_id] = True

    def release(self) -> None:
        self._data.eq_active[self._grasp_id] = False

    @property
    def holding(self) -> bool:
        return bool(self._data.eq_active[self._grasp_id])

    def step(self) -> TwinState:
        """Advance one control tick and return the resulting state."""
        for _ in range(self._steps_per_tick):
            mujoco.mj_step(self._model, self._data)
        self._tick += 1
        return self.state()

    def reset(self) -> None:
        mujoco.mj_resetData(self._model, self._data)
        mujoco.mj_forward(self._model, self._data)
        self._tick = 0

    # ------------------------------------------------------------------ state --
    def _task_status(self) -> TaskStatus:
        """Derived from geometry, never asserted. A demo that reports SUCCESS
        because the script reached its last waypoint is lying."""
        x, y, z = self.cube_position()
        inside_xy = (
            abs(x - BIN_POS[0]) < BIN_INNER_XY[0] and abs(y - BIN_POS[1]) < BIN_INNER_XY[1]
        )
        if inside_xy and z < BIN_SIZE[2]:
            return "success"
        return "running"

    def state(self) -> TwinState:
        cube = self.cube_position()
        cube_quat = self._data.xquat[self._cube_id]  # mujoco is wxyz

        return TwinState(
            timestamp_ns=self.epoch_ns + round(self._tick * 1e9 / self.control_hz),
            scene_id=self.scene_id,
            robot=RobotState(id="robot_01", joint_positions=self.joint_positions()),
            objects=[
                ObjectState(
                    id="cube_01",
                    position_m=cube,
                    orientation_xyzw=(
                        float(cube_quat[1]),
                        float(cube_quat[2]),
                        float(cube_quat[3]),
                        float(cube_quat[0]),
                    ),
                ),
                ObjectState(id="bin_01", position_m=BIN_POS),
            ],
            task=TaskState(id="cube_to_bin", status=self._task_status()),
        )
