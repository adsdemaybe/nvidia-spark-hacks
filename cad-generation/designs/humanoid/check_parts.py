"""Build every part builder in isolation and report solidity and size.

This is the fast inner loop: it catches boolean failures, empty results and
non-manifold output long before the full assembly is generated.

    python check_parts.py [name ...]
"""

from __future__ import annotations

import sys
import traceback

import humanoid_parts as P
import humanoid_params as PR
from humanoid_params import ACTUATORS, ARM, HAND

CASES = {
    "base_plate": lambda: P.base_plate(),
    "column": lambda: P.column(),
    "column_hatch_cover": lambda: P.column_hatch_cover(),
    "torso_shell": lambda: P.torso_shell(),
    "torso_equipment_rails": lambda: P.torso_equipment_rails(),
    "electronics_payload": lambda: P.electronics_payload(),
    "actuator_A80": lambda: P.actuator_module("A80"),
    "actuator_A60": lambda: P.actuator_module("A60"),
    "actuator_A40": lambda: P.actuator_module("A40"),
    "bearing_6810": lambda: P.bearing_ring("6810"),
    "clavicle_bracket": lambda: P.clavicle_bracket("A100"),
    "shoulder_roll_housing": lambda: P.shoulder_roll_housing(),
    "shoulder_yaw_housing": lambda: P.shoulder_yaw_housing(),
    "upper_arm": lambda: P.tube_link(
        ARM["upper_arm_len"],
        ARM["link_od"],
        ARM["link_wall"],
        "A60",
        "A80",
        "upper_arm",
        bottom_style="pitch",
        driven_clear_r=P.crank_joint_radius(ARM["link_od_fore"]),
    ),
    "forearm": lambda: P.cranked_link(
        "A80",
        PR.WRIST_YAW_DROP - PR.ACTUATORS["A40"].length,
        ARM["link_od_fore"],
        ARM["link_wall"],
        "A40",
        "forearm",
    ),
    "forearm_tube": lambda: P.tube_link(
        PR.WRIST_PITCH_DROP,
        ARM["link_od_fore"],
        ARM["link_wall"],
        "A40",
        "A40",
        "forearm_tube",
        bottom_style="pitch",
        windows=False,
        driven_clear_r=P.crank_joint_radius(46.0),
    ),
    "tendon_drive_pack": lambda: P.tendon_drive_pack(),
    "wrist_roll_bracket": lambda: P.cranked_link(
        "A40", PR.WRIST_ROLL_DROP, 46.0, 3.0, "A40", "wrist_roll_bracket",
        bottom_style="roll",
    ),
    "palm": lambda: P.palm(),
    "phalanx_proximal": lambda: P.phalanx(
        HAND["proximal_len"], HAND["finger_w"], HAND["finger_t"], "proximal"
    ),
    "phalanx_distal": lambda: P.phalanx(
        HAND["distal_len"], HAND["finger_w"], HAND["finger_t"], "distal", tip=True
    ),
}


def describe(name, shape):
    solids = shape.solids()
    bb = shape.bounding_box()
    vol = sum(s.volume for s in solids)
    valid = all(s.is_valid() if callable(s.is_valid) else s.is_valid for s in solids)
    size = bb.size
    return (
        f"{name:<26} solids={len(solids):<3} vol={vol/1000:>9.1f} cm3  "
        f"bbox={size.X:>6.1f} x {size.Y:>6.1f} x {size.Z:>6.1f}  "
        f"{'ok' if valid and vol > 0 else 'INVALID'}"
    )


def main(argv):
    names = argv or list(CASES)
    failures = 0
    for name in names:
        try:
            shape = CASES[name]()
            if shape is None or not shape.solids():
                print(f"{name:<26} EMPTY RESULT")
                failures += 1
                continue
            print(describe(name, shape))
        except Exception as exc:  # noqa: BLE001 - this is the diagnostic harness
            failures += 1
            print(f"{name:<26} FAILED: {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=3)
    print(f"\n{len(names) - failures}/{len(names)} parts built")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
