# forearm — 100.0mm between joint axes.
#
# The distal end is an open CRADLE, not a pocket bored into the beam. First
# attempt pocketed the servo straight into the shaft, and the servo is 24.7mm
# wide against a 23.0mm beam: on the upper arm that left 0.35mm side walls holding
# the part together, and on the forearm — narrower still — the pocket was wider
# than the beam and removed the entire wrist end, 77.1mm where 111.5 was intended.
# Both built, both exported, both passed a bore-count gate. The bbox gate below is
# what catches it, which is why every part in this family now carries one.
#
# The cradle is wider than the shaft on purpose. A boss that steps out is
# honest; a wall thinned to keep a silhouette is not.

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

SPAN = 100.0
W, D = 23.0, 20.0

CRADLE_L = SERVO_L + 2 * FIT + 2 * WALL
CRADLE_W = SERVO_W + 2 * FIT + 2 * WALL
CRADLE_H = SERVO_H * 0.6 + WALL

# Shaft, largest flat face on the bed.
part = Pos(SPAN / 2, 0, D / 2) * Box(SPAN + W, W, D)

# Proximal: horn interface to the servo driving this segment, on the -X face.
part -= Pos(0, 0, D / 2) * Rotation(0, 90, 0) * Cylinder(HORN_RECESS_D / 2, HORN_RECESS_H * 2)
for i in range(4):
    a = math.radians(45 + 90 * i)
    part -= Pos(0, HORN_BOLT_CIRCLE / 2 * math.cos(a), D / 2 + HORN_BOLT_CIRCLE / 2 * math.sin(a)) * Rotation(0, 90, 0) * Cylinder(M3_CLEAR_R, W * 4)

# Distal: cradle carrying the next servo, open upward so it drops in and the
# following segment's horn plate traps it.
part += Pos(SPAN, 0, D + CRADLE_H / 2) * Box(CRADLE_L, CRADLE_W, CRADLE_H)
part -= Pos(SPAN, 0, D + WALL + SERVO_H / 2) * Box(SERVO_L + 2 * FIT, SERVO_W + 2 * FIT, SERVO_H)

# Retaining bolts through the cradle cheeks. Positions ASSUMED with the horn
# pattern — no drawing here gives the STS3215's case ears.
for dx in (-SERVO_L / 3, SERVO_L / 3):
    part -= Pos(SPAN + dx, 0, D + WALL + 6.0) * Rotation(90, 0, 0) * Cylinder(M3_CLEAR_R, CRADLE_W * 3)

# Lightening. Mass on this link is mass the shoulder holds all day.
part -= Pos(SPAN * 0.42, 0, D / 2) * Box(SPAN * 0.24, W - 2 * WALL, D * 3)
part -= Pos(SPAN / 2, 0, D) * Box(SPAN * 0.7, 7.0, 3.5)
