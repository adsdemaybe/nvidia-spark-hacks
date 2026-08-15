# NEMA17 stepper mount bracket for the rover chassis, 4mm PLA.
#
# Algebra API rather than the builder API: every cut is positioned in absolute
# coordinates, so "does this feature land on material" is arithmetic a reader can
# check against the spec, not a question about which `with` block is in scope.

T = 4.0        # sheet thickness
W = 50.0       # width, X — both faces
H = 55.0       # vertical face height, Z
D = 40.0       # foot depth, Y

BORE_R = 11.0        # 22mm pilot boss clearance
BOLT_HALF = 15.5     # 31mm square bolt circle
M3_R = 3.4 / 2       # clearance, not nominal thread
M4_R = 4.5 / 2

# The inside corner is the concave edge at (Y=T, Z=T). Every "from the inside
# corner" dimension in the spec is measured from there, which is why T appears
# in each of these and not because of the thickness itself.
PATTERN_Z = T + 32.0
FOOT_HOLE_Y = (T + 12.0, T + 30.0)

# Foot on the XY plane at Z=0 — the largest flat face, so this is the print bed.
foot = Pos(0, D / 2, T / 2) * Box(W, D, T)
wall = Pos(0, T / 2, H / 2) * Box(W, T, H)
part = foot + wall

# The inside corner carries the motor's reaction torque straight into the layer
# boundary between the two faces. Filleted before anything is cut, so the fillet
# has continuous material to run along.
corner = (
    part.edges()
    .filter_by(Axis.X)
    .filter_by_position(Axis.Y, T - 0.01, T + 0.01)
    .filter_by_position(Axis.Z, T - 0.01, T + 0.01)
)
part = fillet(corner, radius=T - 0.1)

# Motor face: pilot bore plus the four M3s, all along -Y, over-length so they cut
# clean through the 4mm wall rather than ending flush with it.
through = 4 * T
part -= Pos(0, T / 2, PATTERN_Z) * Rotation(90, 0, 0) * Cylinder(BORE_R, through)
for dx in (-BOLT_HALF, BOLT_HALF):
    for dz in (-BOLT_HALF, BOLT_HALF):
        part -= (
            Pos(dx, T / 2, PATTERN_Z + dz) * Rotation(90, 0, 0) * Cylinder(M3_R, through)
        )

# Foot: two M4s on the centreline, down through Z.
for y in FOOT_HOLE_Y:
    part -= Pos(0, y, T / 2) * Cylinder(M4_R, through)
