#!/usr/bin/env python3
"""Generate a deterministic mock hand-tracking episode (Shadow Robot Spatial
Demonstration Pipeline spec section 52, Phase 4).

    uv run python tools/make_mock_hand_episode.py

Writes fixtures/spatial-training/hand/mock_episode.jsonl -- ~90 frames @30Hz
of a right hand's wrist approaching a point above the button fixture's
press point, thumb/index tips converging during a "contact" window (driving
gripper 0->1), then retracting. Every canonical joint name (spec section 17;
see ar_contracts.hand_frame.HAND_JOINT_NAMES) is populated every frame --
non-thumb/index joints as fixed offsets from the wrist, a documented
simplification (this fixture only needs to exercise the full HandFrame
schema and give ArmRetargeter/InteractionIR something realistic to chew on,
not be an anatomically faithful hand pose).

Deterministic: no clock, no RNG (SEED exists for consistency with
tools/make_fixtures.py's convention, unused since this generator is purely
arithmetic).
"""

from __future__ import annotations

import json
from pathlib import Path

ARVR_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ARVR_ROOT / "fixtures" / "spatial-training" / "hand" / "mock_episode.jsonl"

SEED = 1337
NS_PER_S = 1_000_000_000
BASE_TS_NS = 1_700_000_000 * NS_PER_S
HZ = 30.0
N_FRAMES = 90

# The button fixture's local press-origin is [0, 0, 0.02] with axis
# [0, 0, -1] (fixtures/spatial-training/assets/button/interaction.json).
# This mock episode doesn't model a real asset placement -- it just aims
# the wrist at a plausible struct_world point above where a button could
# plausibly sit, for schema/pipeline exercise purposes.
BUTTON_WORLD_M = (0.4, 0.0, 0.55)
APPROACH_START_M = (0.2, -0.15, 0.7)
APPROACH_ABOVE_M = 0.06  # wrist hovers this far above the press point
RETRACT_END_M = (0.2, -0.15, 0.7)

PINCH_OPEN_M = 0.08
PINCH_CLOSED_M = 0.015

HAND_JOINT_NAMES = (
    "wrist",
    "thumb-metacarpal", "thumb-phalanx-proximal", "thumb-phalanx-distal", "thumb-tip",
    "index-finger-metacarpal", "index-finger-phalanx-proximal",
    "index-finger-phalanx-intermediate", "index-finger-phalanx-distal", "index-finger-tip",
    "middle-finger-metacarpal", "middle-finger-phalanx-proximal",
    "middle-finger-phalanx-intermediate", "middle-finger-phalanx-distal", "middle-finger-tip",
    "ring-finger-metacarpal", "ring-finger-phalanx-proximal",
    "ring-finger-phalanx-intermediate", "ring-finger-phalanx-distal", "ring-finger-tip",
    "pinky-finger-metacarpal", "pinky-finger-phalanx-proximal",
    "pinky-finger-phalanx-intermediate", "pinky-finger-phalanx-distal", "pinky-finger-tip",
    "palm",
)

IDENTITY_QUAT = (0.0, 0.0, 0.0, 1.0)

# Fixed offset from the wrist for every joint that isn't thumb-tip/
# index-tip (which move independently to drive the pinch). Small,
# plausible, deterministic -- not anatomically exact.
_FIXED_OFFSETS: dict[str, tuple[float, float, float]] = {}
for i, name in enumerate(HAND_JOINT_NAMES):
    if name in ("wrist", "thumb-tip", "index-finger-tip"):
        continue
    finger_index = i % 5
    joint_index = i % 4
    _FIXED_OFFSETS[name] = (
        0.02 + 0.01 * joint_index,
        -0.03 + 0.015 * finger_index,
        0.01 * joint_index,
    )
_FIXED_OFFSETS["palm"] = (0.0, 0.0, -0.01)


Vec3 = tuple[float, float, float]


def _lerp(a: Vec3, b: Vec3, t: float) -> Vec3:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _ease(t: float) -> float:
    """Smoothstep -- avoids a velocity discontinuity at the phase boundary."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _wrist_position(frame: int) -> tuple[float, float, float]:
    above_button = (BUTTON_WORLD_M[0], BUTTON_WORLD_M[1], BUTTON_WORLD_M[2] + APPROACH_ABOVE_M)
    t = frame / (N_FRAMES - 1)
    if t < 0.4:  # approach
        return _lerp(APPROACH_START_M, above_button, _ease(t / 0.4))
    if t < 0.65:  # hold near contact
        return above_button
    # retract
    return _lerp(above_button, RETRACT_END_M, _ease((t - 0.65) / 0.35))


def _scalar_lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _pinch_aperture(frame: int) -> float:
    """Open, closes during the "contact" window (t in [0.4, 0.65]), reopens."""
    t = frame / (N_FRAMES - 1)
    if t < 0.4:
        return PINCH_OPEN_M
    if t < 0.5:
        return _scalar_lerp(PINCH_OPEN_M, PINCH_CLOSED_M, _ease((t - 0.4) / 0.1))
    if t < 0.65:
        return PINCH_CLOSED_M
    if t < 0.75:
        return _scalar_lerp(PINCH_CLOSED_M, PINCH_OPEN_M, _ease((t - 0.65) / 0.1))
    return PINCH_OPEN_M


def make_frame(frame_index: int) -> dict:
    timestamp_ns = BASE_TS_NS + round(frame_index * (NS_PER_S / HZ))
    wrist = _wrist_position(frame_index)
    aperture = _pinch_aperture(frame_index)

    joints: dict[str, dict] = {
        "wrist": {"position_m": list(wrist), "orientation_xyzw": list(IDENTITY_QUAT)},
        "thumb-tip": {
            "position_m": [wrist[0] - aperture / 2, wrist[1], wrist[2] - 0.02],
            "orientation_xyzw": list(IDENTITY_QUAT),
        },
        "index-finger-tip": {
            "position_m": [wrist[0] + aperture / 2, wrist[1], wrist[2] - 0.02],
            "orientation_xyzw": list(IDENTITY_QUAT),
        },
    }
    for name, offset in _FIXED_OFFSETS.items():
        joints[name] = {
            "position_m": [wrist[0] + offset[0], wrist[1] + offset[1], wrist[2] + offset[2]],
            "orientation_xyzw": list(IDENTITY_QUAT),
        }

    return {
        "schema_version": "1.0",
        "timestamp_ns": timestamp_ns,
        "source_device": "mock",
        "hand": "right",
        "frame": "struct_world",
        "joints": joints,
    }


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    frames = [make_frame(i) for i in range(N_FRAMES)]
    OUT_PATH.write_text("\n".join(json.dumps(f) for f in frames) + "\n")
    print(f"wrote {OUT_PATH.relative_to(ARVR_ROOT)} ({len(frames)} frames)")


if __name__ == "__main__":
    main()
