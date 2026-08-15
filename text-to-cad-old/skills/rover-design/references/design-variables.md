# Design Variables

Continuous variables and their bounds live in `roverkit.rover_arm.DESIGN_VARS`.
Discrete choices (`SHOULDER_MOTOR`, `DRIVE_MOTOR`, `SHOULDER_GEAR`) are validated
against the catalogue, not against bounds.

Values outside a bound are **clamped, not rejected**. A clamped proposal usually
means the reasoning about magnitude was wrong, and the evaluation that follows
will not test what was intended. Check `rejected_by_harness` in the report.

| Variable | Units | Primarily moves | Watch out for |
| --- | --- | --- | --- |
| `CHASSIS_L` | mm | payload, bay clearance | Longer chassis adds mass; the bay must still fit |
| `CHASSIS_W` | mm | lateral stability, bay | Width drives track, and the BOM sits across it |
| `AXLE_FRAC` | fraction of L | **payload** | The strongest single lever on tip-over |
| `WHEEL_D` | mm | ground clearance, settling | Changes axle height and therefore CG geometry |
| `LINK1_LEN` | mm | arm_holds, payload, reach | Torque grows faster than length: mass *and* lever |
| `LINK2_LEN` | mm | arm_holds, payload, reach | Same; this is the capability you are trading |
| `TURNTABLE_SETBACK` | mm | payload | Moves the whole arm's lever relative to the axle |
| `BALLAST_M` | kg | payload | Dead mass. Cheap margin, worse payload-to-mass ratio |

## Why `AXLE_FRAC` dominates

The gripper reaches roughly 277 mm from the yaw axis while the front axle sat at
`L/4` — about 52 mm from centre on a 210 mm chassis. That is a lever ratio over
5:1 against a very short base. Moving the axles outward costs nothing in mass
and is almost always the first thing to try when `payload` fails.

This is exactly what the first converged run found, and it took payload from
204 g to 696 g without changing a single dimension of the robot itself.

## Discrete choices

`SHOULDER_MOTOR` and `DRIVE_MOTOR` change **mounting geometry**, not just mass
and torque: frame size, bolt pitch, pilot boss diameter and height, and whether
the mounting holes are tapped or clearance. Switching a NEMA17 for a NEMA23
propagates into every mount cut in the chassis and turntable automatically —
that is the point of the catalogue being parametric. See
`references/catalogue-discipline.md`.
