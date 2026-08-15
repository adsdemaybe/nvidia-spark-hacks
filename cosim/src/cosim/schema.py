"""Message shapes for the virtual wiring harness.

One module, shared by every participant, so the electrical side and the mechanical side
cannot disagree about what a torque message looks like. Pydantic rather than plain dicts
for one reason: **a malformed message must be detectable**. A co-simulation that silently
accepts a field it does not understand, or silently drops one it needed, produces a
plausible trajectory that means nothing.

Every message carries `t` (simulation seconds) and `seq` (the control period index).
`seq` is what the barrier in `clock.py` counts; `t` is what a human reads.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Topic names are constants rather than string literals at call sites: a typo in a
# subscriber's topic is otherwise a silent "no messages ever arrive".
TOPIC_MCU = "mcu/output"
TOPIC_PCB = "pcb/physics"
TOPIC_MOTOR_STATE = "motor/state"
TOPIC_ENCODER = "sensor/encoder"
TOPIC_CONTROL = "sim/control"

ALL_TOPICS = (TOPIC_MCU, TOPIC_PCB, TOPIC_MOTOR_STATE, TOPIC_ENCODER, TOPIC_CONTROL)


class Envelope(BaseModel):
    """What every message has, so the barrier can order them without parsing bodies."""

    t: float = Field(description="simulation time, seconds")
    seq: int = Field(description="control period index, monotonic")


class PinDrive(BaseModel):
    """What the firmware is asking one half-bridge to do."""

    duty: float = Field(ge=0.0, le=1.0)
    freq_hz: float = Field(gt=0.0)
    # Direction is separate from duty because a sign convention buried in a duty value is
    # how a robot ends up driving backwards and nobody can see why.
    direction: Literal[1, -1] = 1


class McuOutput(Envelope):
    """`mcu/output` — the firmware's command for this period."""

    drives: dict[str, PinDrive] = Field(
        default_factory=dict,
        description="keyed by motor id, e.g. {'M1': {...}}",
    )


class MotorElectrical(BaseModel):
    """What the board actually delivered to one motor."""

    voltage_v: float
    current_avg_a: float
    current_peak_a: float
    torque_nm: float = Field(description="at the motor shaft, before any gearbox")
    output_torque_nm: float = Field(description="after the gearbox — what the joint sees")


class PcbPhysics(Envelope):
    """`pcb/physics` — the electrical answer for this period."""

    motors: dict[str, MotorElectrical] = Field(default_factory=dict)
    rail_sag_mv: float = 0.0
    # Warnings travel with the data rather than to a log, because whoever consumes the
    # torque is who needs to know it was produced under protest.
    warnings: list[str] = Field(default_factory=list)


class MotorMechanical(BaseModel):
    """Shaft state — the feedback that makes this a co-simulation."""

    omega_rad_s: float = Field(description="at the motor shaft, after gearbox division")
    angle_rad: float
    load_nm: float = 0.0


class MotorState(Envelope):
    """`motor/state` — what the mechanics report back."""

    motors: dict[str, MotorMechanical] = Field(default_factory=dict)


class JointState(BaseModel):
    angle_rad: float
    vel_rad_s: float


class EncoderReading(Envelope):
    """`sensor/encoder` — what the firmware would actually be able to read."""

    joints: dict[str, JointState] = Field(default_factory=dict)


class Control(Envelope):
    """`sim/control` — the orchestrator's word."""

    cmd: Literal["step", "stop", "reset"]
    reason: str = ""


TOPIC_MODELS: dict[str, type[Envelope]] = {
    TOPIC_MCU: McuOutput,
    TOPIC_PCB: PcbPhysics,
    TOPIC_MOTOR_STATE: MotorState,
    TOPIC_ENCODER: EncoderReading,
    TOPIC_CONTROL: Control,
}


def model_for(topic: str) -> type[Envelope]:
    """The model a topic carries.

    Raises rather than returning a permissive default: an unknown topic means either a
    typo or a participant from a different version, and both should stop the run.
    """
    try:
        return TOPIC_MODELS[topic]
    except KeyError:
        raise KeyError(
            f"unknown topic {topic!r}; known topics are {', '.join(sorted(TOPIC_MODELS))}"
        ) from None
