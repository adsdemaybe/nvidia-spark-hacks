# Stationary arm base — the enclosure the rover-power board lives in.
#
# Every board number here is MEASURED, read out of
# designs/board_reports/rover-power.board_report.json, which pcb-ai derived from
# the routed artifact. Nothing about the board is my invention:
#
#   outline          48.00 x 30.00 mm, 1.4 mm thick
#   mounting holes   (-21.60,-12.60) (21.40,-12.60) (-21.60,12.40) (21.40,6.40)
#   connectors       J1 west (-19.00, 2.50) | J2 north (19.00, 10.50)
#                    J3 south (-8.00,-10.50) | J4 south (8.00,-10.50), all 8.5 mm tall
#
# Note the fourth mounting hole: (21.40, 6.40), not (21.40, 12.40). The pattern is
# NOT a rectangle. A base built to a symmetric four-corner assumption would have
# three standoffs land and one miss by 6 mm, and it would look right in a render.
# The standoffs below go where the holes actually are.

MM = 1.0
BOARD_L, BOARD_W, BOARD_T = 48.0, 30.0, 1.4
BOARD_CLEAR = 1.5          # per side, board to cavity wall
TALLEST_PART = 8.5         # J1..J4 headers
HEADROOM = 2.0
STANDOFF_H = 4.0
STANDOFF_OD = 6.0
STANDOFF_PILOT_R = 2.9 / 2  # M3 self-tapping into PLA: pilot, not clearance

WALL = 3.0
FLOOR = 3.0

# Base footprint. Wide and low because the arm is stationary and cantilevers:
# the base is the counterweight, and its own footprint is the support polygon
# `static_margin` will measure against.
BASE_L, BASE_W = 130.0, 80.0
BASE_H = FLOOR + STANDOFF_H + BOARD_T + TALLEST_PART + HEADROOM   # 18.9

SERVO_L, SERVO_W, SERVO_H = 45.2, 24.7, 35.0    # STS3215, catalogue CONFIRMED
SERVO_FIT = 0.3                                  # per side, print clearance

# Board bay sits toward -X; the yaw servo tower stands at +X.
CAVITY_L = BOARD_L + 2 * BOARD_CLEAR             # 51.0
CAVITY_W = BOARD_W + 2 * BOARD_CLEAR             # 33.0
CAVITY_X = -BASE_L / 2 + WALL + CAVITY_L / 2     # hard against the -X wall
TOWER_X = BASE_L / 2 - WALL - (SERVO_L + 2 * SERVO_FIT) / 2

board_holes = [(-21.60, -12.60), (21.40, -12.60), (-21.60, 12.40), (21.40, 6.40)]
connectors = [
    ("J1", "west", -19.00, 2.50, 4.0, 8.5),
    ("J2", "north", 19.00, 10.50, 4.04, 8.5),
    ("J3", "south", -8.00, -10.50, 4.04, 8.5),
    ("J4", "south", 8.00, -10.50, 4.04, 8.5),
]

# --- solid block, then remove ------------------------------------------------
part = Pos(0, 0, BASE_H / 2) * Box(BASE_L, BASE_W, BASE_H)

# Board cavity, open at the top.
cavity_h = BASE_H - FLOOR
part -= Pos(CAVITY_X, 0, FLOOR + cavity_h / 2) * Box(CAVITY_L, CAVITY_W, cavity_h)

# Standoffs rise from the cavity floor at the real hole positions.
for hx, hy in board_holes:
    x = CAVITY_X + hx
    part += Pos(x, hy, FLOOR + STANDOFF_H / 2) * Cylinder(STANDOFF_OD / 2, STANDOFF_H)
    part -= Pos(x, hy, FLOOR + STANDOFF_H / 2) * Cylinder(STANDOFF_PILOT_R, STANDOFF_H * 3)

# Connector cutouts. The opening spans from the seated board's top face upward,
# so the header clears the wall rather than the wall clearing the header.
seat_z = FLOOR + STANDOFF_H + BOARD_T
for ref, edge, cx, cy, cw, ch in connectors:
    opening_h = ch + 1.0
    z = seat_z + opening_h / 2
    pad = 1.0
    if edge in ("north", "south"):
        y = (CAVITY_W / 2 + WALL) * (1 if edge == "north" else -1)
        part -= Pos(CAVITY_X + cx, y, z) * Box(cw + 2 * pad, WALL * 4, opening_h)
    else:
        x = CAVITY_X - CAVITY_L / 2 - WALL / 2
        part -= Pos(x, cy, z) * Box(WALL * 4, cw + 2 * pad, opening_h)

# --- yaw servo tower ---------------------------------------------------------
# The servo drops in from the top, output shaft up. Pocket from the catalogue's
# CONFIRMED body size plus a print fit; no bolt pattern is invented, because no
# datasheet here gives one — the servo is retained by the shoulder yoke bolting
# down over it (see shoulder_yoke.py).
pocket_l = SERVO_L + 2 * SERVO_FIT
pocket_w = SERVO_W + 2 * SERVO_FIT
tower_h = BASE_H + SERVO_H * 0.55
part += Pos(TOWER_X, 0, tower_h / 2) * Box(pocket_l + 2 * WALL, pocket_w + 2 * WALL, tower_h)
part -= Pos(TOWER_X, 0, tower_h - SERVO_H / 2 + 0.01) * Box(pocket_l, pocket_w, SERVO_H)

# Cable route from the servo pocket down into the board bay.
part -= Pos(TOWER_X - pocket_l / 2 - WALL / 2, 0, tower_h - SERVO_H / 2) * Box(WALL * 3, 10.0, 12.0)

# --- feet --------------------------------------------------------------------
# M4 clearance, so the base can actually be bolted to a bench. A stationary arm
# that is not fixed down is a mobile arm with extra steps.
for fx in (-BASE_L / 2 + 10, BASE_L / 2 - 10):
    for fy in (-BASE_W / 2 + 10, BASE_W / 2 - 10):
        part -= Pos(fx, fy, FLOOR / 2) * Cylinder(4.5 / 2, FLOOR * 4)

# Break the outer vertical arris. Printed corners this sharp chip in handling.
outer = (
    part.edges()
    .filter_by(Axis.Z)
    .filter_by_position(Axis.X, BASE_L / 2 - 0.01, BASE_L / 2 + 0.01)
)
part = fillet(outer, radius=2.0)
