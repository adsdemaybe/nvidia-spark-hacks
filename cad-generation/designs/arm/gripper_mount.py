# Gripper mount — the tool plate at the end of the wrist. The arm terminates in
# a documented bolt pattern rather than in whatever the last part happened to be.

# Shared across every arm part. Repeated in each file rather than imported,
# because freeform runs each file alone in a subprocess with no filesystem.
SERVO_L, SERVO_W, SERVO_H = 45.2, 24.7, 35.0   # STS3215, catalogue CONFIRMED
FIT = 0.3                                       # per side, print clearance
WALL = 3.0
BEAM_W, BEAM_D = 26.0, 22.0                     # arm cross-section
# Servo horn interface. The STS3215's spline and horn bolt circle are NOT in any
# datasheet available here, so this pattern is ASSUMED: a recess for the horn
# disc plus four M3 clearance holes on a 20mm circle. Every part in the family
# uses the same one, so they interchange -- and when a real drawing turns up,
# it changes in five identical places rather than five different ones.
HORN_RECESS_D, HORN_RECESS_H = 24.0, 2.5
HORN_BOLT_CIRCLE, M3_CLEAR_R = 20.0, 3.4 / 2

PLATE_L, PLATE_W, PLATE_T = 40.0, 34.0, 5.0

part = Pos(0, 0, PLATE_T / 2) * Box(PLATE_L, PLATE_W, PLATE_T)

# Horn interface to the wrist servo.
part -= Pos(0, 0, HORN_RECESS_H / 2) * Cylinder(HORN_RECESS_D / 2, HORN_RECESS_H)
for i in range(4):
    a = math.radians(45 + 90 * i)
    part -= Pos(HORN_BOLT_CIRCLE / 2 * math.cos(a), HORN_BOLT_CIRCLE / 2 * math.sin(a), PLATE_T / 2) * Cylinder(M3_CLEAR_R, PLATE_T * 4)

# Tool pattern: four M4 on a 25mm square, the face a gripper bolts to.
for sx in (-1, 1):
    for sy in (-1, 1):
        part -= Pos(sx * 12.5, sy * 12.5, PLATE_T / 2) * Cylinder(4.5 / 2, PLATE_T * 4)

part -= Pos(0, 0, PLATE_T / 2) * Cylinder(4.0, PLATE_T * 4)
