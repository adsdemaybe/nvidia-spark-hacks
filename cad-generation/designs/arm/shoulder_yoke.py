# Shoulder yoke — bolts to the yaw servo's horn and carries the shoulder-pitch
# servo. Also the yaw servo's retainer: it caps the base tower, which is why the
# base invents no servo bolt pattern (see base_enclosure.py).

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

PLATE_L, PLATE_W, PLATE_T = 58.0, 34.0, 6.0
ARM_H = SERVO_W + 2 * FIT + 2 * WALL    # tall enough to straddle the pitch servo
GAP = SERVO_L + 2 * FIT                 # clear span between the cheeks

plate = Pos(0, 0, PLATE_T / 2) * Box(PLATE_L, PLATE_W, PLATE_T)

# Two cheeks rising to hold the pitch servo across the gap.
cheek_t = WALL + 1.0
for sx in (-1, 1):
    x = sx * (GAP / 2 + cheek_t / 2)
    plate += Pos(x, 0, PLATE_T + ARM_H / 2) * Box(cheek_t, PLATE_W, ARM_H)

part = plate

# Pitch servo pocket, straddled between the cheeks: a through slot, so the servo
# slides in from the front and is trapped by the upper arm bolting onto its horn.
part -= Pos(0, 0, PLATE_T + ARM_H / 2) * Box(GAP, SERVO_W + 2 * FIT, SERVO_H)

# Horn interface to the yaw servo below.
part -= Pos(0, 0, HORN_RECESS_H / 2) * Cylinder(HORN_RECESS_D / 2, HORN_RECESS_H)
for i in range(4):
    a = math.radians(45 + 90 * i)
    part -= Pos(HORN_BOLT_CIRCLE / 2 * math.cos(a), HORN_BOLT_CIRCLE / 2 * math.sin(a), PLATE_T / 2) * Cylinder(M3_CLEAR_R, PLATE_T * 4)

# Cable pass-through down the axis of rotation, so the loom does not wind up.
part -= Pos(0, 0, PLATE_T / 2) * Cylinder(5.0, PLATE_T * 4)
