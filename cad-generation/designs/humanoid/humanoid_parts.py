"""Part builders for the stationary dual-arm humanoid torso.

Every function returns a closed solid (or a labelled Compound of solids) in its
own local frame. The docstring of each builder states that frame, because the
assembly script places parts by those datums rather than by trial and error.

Shared conventions:
  * Actuator interfaces are a pilot spigot bore plus a bolt circle, so a joint
    module locates on the pilot and is clamped by the bolts.
  * Rotating shells are separated from fixed shells by ``JOINT_GAP``.
  * Cable routing is a real through-path: every joint has a bore at least
    ``Actuator.cable_od`` clear.
"""

from __future__ import annotations

import math

from build123d import (
    Align,
    Axis,
    Box,
    Cylinder,
    Location,
    Locations,
    Plane,
    PolarLocations,
    Pos,
    Rot,
    SortBy,
    Cone,
    Compound,
    Sphere,
    chamfer,
    fillet,
)

from humanoid_params import (
    ACTUATORS,
    ARM,
    BASE_PLATE,
    BEARINGS,
    BEARING_SEAT_DEPTH_EXTRA,
    CABLE_CHANNEL,
    COLUMN,
    COVER_WALL,
    DENSITY,
    FINGER_SERVO,
    HAND,
    JOINT_GAP,
    TORSO,
    WALL,
    Actuator,
    Bearing,
    clearance_hole,
    tap_drill,
)

BOTTOM = (Align.CENTER, Align.CENTER, Align.MIN)
TOP = (Align.CENTER, Align.CENTER, Align.MAX)


# --------------------------------------------------------------------------
# Generic mechanical features
# --------------------------------------------------------------------------


def _cyl(d: float, h: float, align=BOTTOM):
    return Cylinder(radius=d / 2, height=h, align=align)


def _at(locations, shape):
    """Fuse a copy of ``shape`` at every location into one solid.

    ``Locations * shape`` yields a plain list in algebra mode, which cannot be
    transformed or subtracted further; fusing gives back a normal operand.
    """
    placed = locations * shape
    if not isinstance(placed, (list, tuple)):
        return placed
    fused = placed[0]
    for item in placed[1:]:
        fused += item
    return fused


def bolt_circle(diameter: float, count: float, hole_d: float, depth: float, start: float = 0.0):
    """A ring of vertical holes, base at z=0, drilled upward."""
    return _at(
        PolarLocations(diameter / 2, int(count), start_angle=start), _cyl(hole_d, depth)
    )


def actuator_interface_cut(act: Actuator, depth: float, tapped: bool = True):
    """Material to remove from a plate so an actuator can bolt to it.

    Local frame: bolt-circle axis on +Z, plate face at z=0, cut going up.
    Gives a pilot bore sized to the module's rotating flange plus running
    clearance, and the fastener holes.
    """
    pilot = _cyl(act.flange_od + 2 * JOINT_GAP, depth)
    hole_d = tap_drill(act.bolt_size) if tapped else clearance_hole(act.bolt_size)
    return pilot + bolt_circle(act.bolt_circle, act.n_bolts, hole_d, depth)


def bearing_seat_cut(b: Bearing, depth: float | None = None):
    """Housing bore for a deep-groove bearing, seat face at z=0 going up."""
    depth = depth if depth is not None else b.width + BEARING_SEAT_DEPTH_EXTRA
    seat = _cyl(b.od, depth)
    # Shoulder relief so the outer race seats on a defined face, and a
    # through-bore for the shaft plus an extraction relief under the race.
    relief = Pos(0, 0, -b.shoulder) * _cyl(b.od - 2 * b.shoulder, b.shoulder)
    return seat + relief


def actuator_module(key: str, angle: float = 0.0):
    """Envelope model of a purchased strain-wave joint module.

    Local frame: rotation axis = Z, mounting face (stator) at z=0, output
    flange at z=+length. ``angle`` spins the output flange so a posed assembly
    shows the real joint position.
    """
    act = ACTUATORS[key]
    # The output flange turns relative to the stator, so it stands off by the
    # seal gap rather than sharing a face with it.
    seal_gap = 0.6
    body_h = act.length - act.flange_thk - seal_gap

    housing = _cyl(act.housing_od, body_h)
    housing -= _cyl(act.bore, body_h)  # hollow shaft for the cable bundle
    housing = chamfer(housing.edges().filter_by(Plane.XY).group_by(Axis.Z)[-1], 1.5)

    flange_z = body_h + seal_gap
    flange = Pos(0, 0, flange_z) * _cyl(act.flange_od, act.flange_thk)
    flange -= Pos(0, 0, flange_z) * _cyl(act.bore, act.flange_thk)
    # Tapped holes in the output flange that the driven link bolts into.
    flange -= Pos(0, 0, flange_z) * bolt_circle(
        act.bolt_circle * act.flange_od / act.housing_od,
        act.n_bolts,
        tap_drill(act.bolt_size),
        act.flange_thk,
    )
    flange = Rot(0, 0, angle) * flange

    # Stator mounting holes, so the module is a real bolt-up interface.
    housing -= bolt_circle(act.bolt_circle, act.n_bolts, tap_drill(act.bolt_size), 10.0)

    return Compound(
        label=f"{act.name}_actuator",
        children=[
            _label(housing, f"{act.name}_stator"),
            _label(flange, f"{act.name}_output_flange"),
        ],
    )


def bearing_ring(designation: str):
    """Simplified bearing solid: outer race, inner race, axis on Z, centred.

    Race thickness scales with the radial section so small bearings (623) do
    not end up with races that overlap each other across the ball track.
    """
    b = BEARINGS[designation]
    section = (b.od - b.bore) / 2
    race = min(2.2, section / 3.0)
    outer = Cylinder(radius=b.od / 2, height=b.width) - Cylinder(
        radius=b.od / 2 - race, height=b.width
    )
    inner = Cylinder(radius=b.bore / 2 + race, height=b.width) - Cylinder(
        radius=b.bore / 2, height=b.width
    )
    return Compound(
        label=f"bearing_{designation}",
        children=[_label(outer, "outer_race"), _label(inner, "inner_race")],
    )


def _label(shape, name: str):
    shape.label = name
    return shape


def _fillet_vertical(part, radius: float, count: int | None = None):
    """Round the tallest vertical edges; tolerant of edge-count changes."""
    edges = part.edges().filter_by(Axis.Z)
    if not edges:
        return part
    if count is not None:
        edges = edges.group_by(SortBy.LENGTH)[-1]
    try:
        return fillet(edges, radius)
    except Exception:
        return part


# --------------------------------------------------------------------------
# Fixed base
# --------------------------------------------------------------------------


def base_plate():
    """Bolt-down steel floor plate.

    Local frame: origin at footprint centre, z=0 at the underside (floor).
    """
    p = BASE_PLATE
    plate = Box(p["size"], p["size"], p["thk"], align=BOTTOM)
    plate = _fillet_vertical(plate, p["corner_r"])

    # M12 wedge anchors, one per corner, with a spotface for the washer.
    inset = p["size"] / 2 - p["anchor_inset"]
    anchors = Locations(
        (-inset, -inset), (inset, -inset), (-inset, inset), (inset, inset)
    )
    plate -= _at(anchors, _cyl(clearance_hole(p["anchor_bolt"]), p["thk"]))
    plate -= _at(anchors, Pos(0, 0, p["thk"] - 1.6) * _cyl(p["anchor_washer_od"], 1.6))

    # Cable entry from below and the tapped pattern the column flange bolts to.
    plate -= _cyl(60.0, p["thk"])
    cb = COLUMN["flange_size"] / 2 - COLUMN["flange_bolt_inset"]
    col_bolts = Locations((-cb, -cb), (cb, -cb), (-cb, cb), (cb, cb))
    plate -= _at(
        col_bolts,
        Pos(0, 0, p["thk"] - 14.0) * _cyl(tap_drill(COLUMN["flange_bolt"]), 14.0),
    )

    plate.label = "base_plate_steel"
    return plate


def column():
    """Pedestal column: square tube with bolted flanges top and bottom.

    Local frame: origin on the column axis, z=0 at the bottom flange underside.
    """
    c = COLUMN
    total = c["flange_thk"] * 2 + c["height"]

    tube = Box(c["width"], c["width"], total, align=BOTTOM)
    tube = _fillet_vertical(tube, c["corner_r"])
    cavity = Box(
        c["width"] - 2 * c["wall"], c["width"] - 2 * c["wall"], total, align=BOTTOM
    )
    tube -= cavity

    flange_bottom = Box(c["flange_size"], c["flange_size"], c["flange_thk"], align=BOTTOM)
    flange_top = Pos(0, 0, total - c["flange_thk"]) * Box(
        c["flange_size"], c["flange_size"], c["flange_thk"], align=BOTTOM
    )
    body = tube + flange_bottom + flange_top
    body = _fillet_vertical(body, 8.0)

    # Keep the wiring bore open through both flanges.
    body -= _cyl(c["width"] - 2 * c["wall"] - 8.0, total)

    cb = c["flange_size"] / 2 - c["flange_bolt_inset"]
    bolts = Locations((-cb, -cb), (cb, -cb), (-cb, cb), (cb, cb))
    hole = _cyl(clearance_hole(c["flange_bolt"]), c["flange_thk"])
    body -= _at(bolts, hole)
    body -= _at(bolts, Pos(0, 0, total - c["flange_thk"]) * hole)

    # Rear service hatch: opening plus the tapped border it closes onto.
    hatch = Pos(0, -c["width"] / 2, c["flange_thk"] + c["access_z"]) * Box(
        c["access_w"], 3 * c["wall"], c["access_h"], align=BOTTOM
    )
    body -= hatch

    body.label = "pedestal_column"
    return body


def column_hatch_cover():
    """Bolt-on cover for the column service opening. Origin at cover centre."""
    c = COLUMN
    cover = Box(c["access_w"] + 30.0, COVER_WALL, c["access_h"] + 30.0)
    cover.label = "column_hatch_cover"
    return cover


# --------------------------------------------------------------------------
# Torso
# --------------------------------------------------------------------------


def torso_shell():
    """Structural torso enclosure holding the electronics and both shoulders.

    Local frame: origin at the footprint centre, z=0 at the outer underside.
    Bottom is open and lands on the column top flange; top is closed.
    """
    t = TORSO
    w, d, h, wall = t["width"], t["depth"], t["height"], t["wall"]

    shell = Box(w, d, h, align=BOTTOM)
    shell = _fillet_vertical(shell, t["corner_r"])
    # Hollow it, leaving the top face closed and the bottom open.
    shell -= Box(w - 2 * wall, d - 2 * wall, h - wall, align=BOTTOM)

    # Column spigot: the torso locates on the column flange bolt pattern.
    cb = COLUMN["flange_size"] / 2 - COLUMN["flange_bolt_inset"]
    bolts = Locations((-cb, -cb), (cb, -cb), (-cb, cb), (cb, cb))
    shell += _at(bolts, _cyl(clearance_hole(COLUMN["flange_bolt"]) + 10.0, 12.0))
    shell -= _at(bolts, _cyl(clearance_hole(COLUMN["flange_bolt"]), 14.0))

    # Side vent slots, both walls, below the shoulder mounts.
    slot_z = 60.0
    pitch = 18.0
    span = (t["vent_count"] - 1) * pitch
    for sign in (-1, 1):
        for i in range(t["vent_count"]):
            y = -span / 2 + i * pitch
            shell -= Pos(sign * w / 2, y, slot_z) * Rot(0, 90, 0) * _cyl(
                t["vent_slot_w"], 3 * wall, align=(Align.CENTER, Align.CENTER, Align.CENTER)
            )

    # Rear extraction fan with its M4 pattern.
    fan_z = h - 90.0
    fan = Pos(0, -d / 2, fan_z) * Rot(90, 0, 0) * _cyl(t["fan_size"] - 4.0, 3 * wall)
    shell -= fan
    fb = 71.5 / 2  # 80 mm fan bolt pattern
    for fx, fz in ((-fb, -fb), (fb, -fb), (-fb, fb), (fb, fb)):
        shell -= Pos(fx, -d / 2 + wall, fan_z + fz) * Rot(90, 0, 0) * _cyl(
            tap_drill(4.0), 3 * wall
        )

    # Shoulder mounting pads: a raised boss on each side wall carrying the
    # clavicle bracket, with a bore for the shoulder cable loom.
    pad_od = ACTUATORS["A80"].housing_od + 34.0
    for sign in (-1, 1):
        pad = Pos(sign * (w / 2 - wall), 0, t["shoulder_z"]) * Rot(0, sign * 90, 0) * _cyl(
            pad_od, wall + 8.0
        )
        shell += pad
        shell -= Pos(sign * (w / 2 - wall - 2), 0, t["shoulder_z"]) * Rot(
            0, sign * 90, 0
        ) * _cyl(34.0, wall + 14.0)
        pcd = pad_od - 20.0
        for j in range(6):
            a = math.radians(j * 60.0 + 30.0)
            py, pz = pcd / 2 * math.cos(a), pcd / 2 * math.sin(a)
            shell -= Pos(sign * (w / 2 - wall - 2), py, t["shoulder_z"] + pz) * Rot(
                0, sign * 90, 0
            ) * _cyl(tap_drill(5.0), wall + 14.0)

    shell.label = "torso_shell"
    return shell


def torso_equipment_rails():
    """Internal L-rails and PCB standoffs sized from the payload list.

    Local frame matches ``torso_shell`` (origin at footprint centre, z=0 at the
    outer underside). Standoffs are placed from each component's mount list, so
    moving a board in the parameters moves its mounting hardware with it.
    """
    from humanoid_params import PAYLOAD

    t = TORSO
    wall = t["wall"]
    parts = []

    # Two vertical L-rails per side that the boards' standoffs land on.
    rail_h = t["height"] - wall - 20.0
    for sx in (-1, 1):
        x = sx * (t["width"] / 2 - wall - t["rail_thk"] / 2)
        web = Pos(x, 0, 10.0) * Box(
            t["rail_thk"], t["depth"] - 2 * wall - 10.0, rail_h, align=BOTTOM
        )
        lip = Pos(x - sx * 9.0, 0, 10.0) * Box(
            18.0, t["rail_thk"], rail_h, align=BOTTOM
        )
        parts.append(_label(web + lip, f"equipment_rail_{'right' if sx > 0 else 'left'}"))

    # A standoff under every board mounting hole.
    for comp in PAYLOAD:
        if not comp.mount:
            continue
        cx, cy, cz = comp.pos
        base_z = wall + cz - comp.size[2] / 2 - t["standoff_h"]
        for mx, my in comp.mount:
            post = Pos(cx + mx, cy + my, base_z) * _cyl(t["standoff_od"], t["standoff_h"])
            post -= Pos(cx + mx, cy + my, base_z) * _cyl(
                tap_drill(3.0), t["standoff_h"]
            )
            parts.append(_label(post, f"standoff_{comp.name}"))

    # Battery tray: a floor with retaining walls under the pack.
    from humanoid_params import PAYLOAD as _P

    pack = next(c for c in _P if c.name == "battery_pack")
    px, py, pz = pack.pos
    bw, bd, bh = pack.size
    tray_z = wall + pz - bh / 2 - 4.0
    tray = Pos(px, py, tray_z) * Box(bw + 12.0, bd + 12.0, 4.0, align=BOTTOM)
    tray += Pos(px, py, tray_z) * (
        Box(bw + 12.0, bd + 12.0, 26.0, align=BOTTOM)
        - Box(bw + 1.0, bd + 1.0, 26.0, align=BOTTOM)
    )
    parts.append(_label(tray, "battery_tray"))

    # Cable channel from the column bore up the rear wall to the shoulders.
    ch = CABLE_CHANNEL
    chan_h = t["shoulder_z"] + 40.0
    channel = Pos(0, -t["depth"] / 2 + wall + ch["depth"] / 2, wall) * (
        Box(ch["width"] + 2 * ch["wall"], ch["depth"] + ch["wall"], chan_h, align=BOTTOM)
        - Box(ch["width"], ch["depth"], chan_h, align=BOTTOM)
    )
    parts.append(_label(channel, "cable_channel"))

    return Compound(label="torso_equipment", children=parts)


def electronics_payload():
    """Envelope solids for the boards, pack and converters actually installed.

    These are keep-out models, not detailed component CAD; they exist so the
    enclosure is validated against real hardware volumes and mounting patterns.
    """
    from humanoid_params import PAYLOAD

    wall = TORSO["wall"]
    parts = []
    for comp in PAYLOAD:
        l, w, hgt = comp.size
        body = Pos(comp.pos[0], comp.pos[1], wall + comp.pos[2]) * Box(l, w, hgt)
        parts.append(_label(body, comp.name))
    return Compound(label="electronics_payload", children=parts)




# --------------------------------------------------------------------------
# Arm links
#
# One rule governs every joint: a joint frame's origin is the OUTPUT FACE of
# that joint's actuator module, on the joint axis. Each module's stator body
# therefore sits on the *parent* side of the frame, and the driven link bolts
# to the face at the origin. Links elbow around those module bodies, which is
# why the shoulder and elbow brackets are cranked rather than straight.
#
# Structural members are shells, not solid blocks. The first mass pass built
# them solid and the shoulder came out at 33 Nm static against a 40 Nm module,
# so every housing here is a wall of ARM["yoke_wall"] around an open cavity
# that doubles as the cable route.
# --------------------------------------------------------------------------


def _output_bolt_circle(act: Actuator) -> float:
    """Bolt circle on a module's rotating output flange."""
    return act.bolt_circle * act.flange_od / act.housing_od


def output_hub(act: Actuator, flange_t: float, spin=None, od: float | None = None):
    """Disc that bolts onto a module's output flange.

    ``spin`` orients the disc: None leaves the axis on +Z, ``Rot(0, 90, 0)``
    puts it on +X, ``Rot(-90, 0, 0)`` on +Y. The disc occupies the first
    ``flange_t`` along that axis.

    ``od`` widens the disc past the bolt circle. A tube link MUST pass its own
    outside diameter here: a hub narrower than the tube bore sits inside the
    tube without touching it and comes out as a separate free-floating solid.
    """
    spin = spin if spin is not None else Rot(0, 0, 0)
    hub = spin * _cyl(max(od or 0.0, act.flange_od + 16.0), flange_t)
    hub -= spin * _cyl(act.bore + 4.0, flange_t)
    hub -= spin * bolt_circle(
        _output_bolt_circle(act), act.n_bolts, clearance_hole(act.bolt_size), flange_t
    )
    return hub


def module_shroud(act: Actuator, wall: float, extra: float = 0.0):
    """Shell wrapping a module's stator, axis on +Z, seat face at z=0.

    The module drops in from z=0; the shell caps nothing, so this is an open
    sleeve that carries the bolt loads into the surrounding structure.
    """
    length = act.length + extra
    sleeve = _cyl(act.housing_od + 2 * JOINT_GAP + 2 * wall, length)
    sleeve -= _cyl(act.housing_od + 2 * JOINT_GAP, length)
    return sleeve


def clavicle_bracket(act_key: str = "A100"):
    """Bolts the shoulder-pitch module to the torso side wall.

    Local frame: pitch axis on X, origin at the torso wall's outer face. The
    module stator seats at x = ``mount_thk`` and its output face lands at
    x = ARM["clavicle_len"], which is where the arm chain starts.
    """
    act = ACTUATORS[act_key]
    mount_thk = ARM["clavicle_len"] - act.length
    if mount_thk < 8.0:
        raise ValueError(
            f"ARM['clavicle_len']={ARM['clavicle_len']} leaves only {mount_thk} mm "
            f"of mounting plate for a {act.length} mm {act.name}"
        )
    pad_od = act.housing_od + 34.0

    body = Rot(0, 90, 0) * _cyl(pad_od, mount_thk)
    body += Pos(mount_thk, 0, 0) * Rot(0, 90, 0) * module_shroud(act, WALL, extra=-act.length + 20.0)

    # Clearance holes onto the torso shoulder pad pattern.
    pcd = pad_od - 20.0
    for j in range(6):
        a = math.radians(j * 60.0 + 30.0)
        body -= Pos(0, pcd / 2 * math.cos(a), pcd / 2 * math.sin(a)) * Rot(
            0, 90, 0
        ) * _cyl(clearance_hole(5.0), mount_thk)

    body -= Rot(0, 90, 0) * actuator_interface_cut(act, mount_thk + 1.0)
    body -= Rot(0, 90, 0) * _cyl(act.bore, mount_thk)
    return _label(body, "clavicle_bracket")


def shoulder_roll_housing(pitch_key: str = "A100", roll_key: str = "A100"):
    """Pitch output -> roll (abduction) module.

    Local frame: origin on the pitch axis at the pitch module's output face.
    The roll axis is on Y through (dx, 0, 0); the roll module's body occupies
    y in [-length, 0] so its output face lands on the arm centre plane.

    Built as hub + hollow crank arm + module shroud, so the section carrying
    the shoulder moment is a closed box rather than a billet.
    """
    pitch = ACTUATORS[pitch_key]
    roll = ACTUATORS[roll_key]
    dx = ARM["shoulder_roll_dx"]
    w = ARM["yoke_wall"]
    flange_t = 12.0

    body = output_hub(pitch, flange_t, Rot(0, 90, 0))

    # Shroud around the roll module, axis on Y, seat face at y = -length.
    body += Pos(dx, -roll.length, 0) * Rot(-90, 0, 0) * module_shroud(roll, w)
    # Back plate the roll stator bolts to.
    body += Pos(dx, -roll.length - w, 0) * Rot(-90, 0, 0) * _cyl(
        roll.housing_od + 2 * JOINT_GAP + 2 * w, w
    )

    # Hollow crank arm from the hub across to the shroud.
    arm_w = roll.flange_od + 2 * w
    arm_h = roll.housing_od * 0.72
    body += Pos(dx / 2, -roll.length / 2, 0) * Box(dx, arm_w, arm_h)
    cavity_len = dx - flange_t + 24.0
    body -= Pos(flange_t + cavity_len / 2, -roll.length / 2, 0) * Box(
        cavity_len, arm_w - 2 * w, arm_h - 2 * w
    )
    try:
        body = fillet(body.edges().filter_by(Axis.X).group_by(SortBy.LENGTH)[-1], 8.0)
    except Exception:
        pass  # cosmetic only; the crank is valid without it

    # Roll module interface and the loom path through to it.
    body -= Pos(dx, -roll.length - w, 0) * Rot(-90, 0, 0) * actuator_interface_cut(
        roll, roll.length + w + 2.0
    )
    body -= Rot(0, 90, 0) * _cyl(18.0, dx)
    return _label(body, "shoulder_roll_housing")


def shoulder_yaw_housing(roll_key: str = "A100", yaw_key: str = "A60"):
    """Roll output -> humeral-rotation (yaw) module.

    Local frame: origin on the roll axis at the roll module's output face
    (y = 0). The yaw module hangs below with its output face at
    z = -ARM["shoulder_yaw_drop"].
    """
    roll = ACTUATORS[roll_key]
    yaw = ACTUATORS[yaw_key]
    drop = ARM["shoulder_yaw_drop"]
    seat_z = -(drop - yaw.length)
    w = ARM["yoke_wall"]
    flange_t = 12.0

    body = output_hub(roll, flange_t, Rot(-90, 0, 0))

    # Hollow saddle turning the drive from the Y axis down onto the Z axis.
    section = yaw.housing_od + 2 * JOINT_GAP + 2 * w
    depth = roll.flange_od + 16.0
    body += Pos(0, flange_t / 2, seat_z / 2) * Box(section, depth, abs(seat_z))
    body -= Pos(0, flange_t / 2, seat_z / 2 + w) * Box(
        section - 2 * w, depth - 2 * w, abs(seat_z)
    )
    body = _fillet_vertical(body, 8.0)

    body += Pos(0, 0, seat_z) * module_shroud(yaw, w)
    body -= Pos(0, 0, seat_z) * actuator_interface_cut(yaw, abs(seat_z) + 2.0)
    body -= Pos(0, 0, seat_z) * _cyl(yaw.bore + 4.0, abs(seat_z) + flange_t)
    return _label(body, "shoulder_yaw_housing")


def tube_link(
    length: float,
    od: float,
    wall: float,
    top_act: str,
    bottom_act: str | None,
    label: str,
    bottom_style: str = "inline",
    windows: bool = True,
    driven_clear_r: float = 0.0,
):
    """Structural limb tube: bolts to a module output on top, carries the next.

    Local frame: tube axis on Z, origin at the driving module's output face,
    tube running down to z = -length.

    ``bottom_style``:
      * ``"inline"`` - the next module's axis is Z; its stator seats on the
        bottom face.
      * ``"pitch"``  - the next module's axis is X; the link ends in a cheek
        plate normal to X so the module body sits inboard and its output face
        lands on the arm centre plane.
    """
    top = ACTUATORS[top_act]
    flange_t = 10.0
    cheek_t = 9.0

    # A pitch joint at the far end needs the tube to STOP short of the axis:
    # the driven link sweeps a cylinder of driven_clear_r about it, and a tube
    # running all the way down sits inside that sweep.
    lift = (driven_clear_r + JOINT_GAP) if bottom_style == "pitch" else 0.0
    tube_len = length - lift

    tube = Pos(0, 0, -tube_len) * _cyl(od, tube_len)
    tube -= Pos(0, 0, -tube_len) * _cyl(od - 2 * wall, tube_len)
    body = tube + Pos(0, 0, -flange_t) * output_hub(top, flange_t, od=od)
    body -= Pos(0, 0, -flange_t) * _cyl(top.bore + 4.0, flange_t)

    if bottom_act and bottom_style == "inline":
        bot = ACTUATORS[bottom_act]
        seat = Pos(0, 0, -length) * _cyl(max(od, bot.housing_od + 14.0), flange_t)
        seat -= Pos(0, 0, -length) * actuator_interface_cut(bot, flange_t + 1.0)
        seat -= Pos(0, 0, -length) * _cyl(bot.bore + 3.0, flange_t)
        body += seat

    elif bottom_act and bottom_style == "pitch":
        bot = ACTUATORS[bottom_act]
        plate_x = -(bot.length + cheek_t)
        # Gusset between the cheek plate and the OUTSIDE of the tube. An
        # earlier version spanned across to +od/2, which put fixed structure
        # inside the tube bore (where the tendon pack lives) and inside the
        # driven link's swept volume.
        span = abs(plate_x) - od / 2 + wall
        depth = bot.housing_od + 16.0
        height = od / 2 + 26.0

        # Cheek plate normal to X plus a hollow fairing blending into the tube.
        body += Pos(plate_x, 0, -length) * Rot(0, 90, 0) * _cyl(depth, cheek_t)
        # Gusset starts above the driven link's swept radius.
        blend = Pos((plate_x - od / 2 + wall) / 2, 0, -length + lift + height / 2) * Box(
            span, depth, height
        )
        blend = fillet(blend.edges().filter_by(Axis.X), 10.0)
        body += blend
        # Hollow only the bracket arm outboard of the tube; a cavity spanning
        # the full width would sever the tube from the fairing.
        cav_x0 = plate_x + wall
        cav_x1 = -od / 2 - wall
        if cav_x1 - cav_x0 > 2 * wall:
            body -= Pos(
                (cav_x0 + cav_x1) / 2, 0, -length + lift + height / 2
            ) * Box(cav_x1 - cav_x0, depth - 2 * ARM["yoke_wall"], height - 2 * wall)

        body -= Pos(plate_x, 0, -length) * Rot(0, 90, 0) * actuator_interface_cut(
            bot, cheek_t + 1.0
        )
        body -= Pos(plate_x, 0, -length) * Rot(0, 90, 0) * _cyl(bot.bore, cheek_t)

    if windows and tube_len > 150.0:
        # Lightening cut-outs on the front and rear faces, on the neutral axis
        # of the dominant (sagittal) bending plane.
        win_h = min(120.0, tube_len - 110.0)
        for sign in (-1, 1):
            slot = Pos(0, sign * od / 2, -tube_len / 2) * Box(
                26.0, od, win_h, align=(Align.CENTER, Align.CENTER, Align.CENTER)
            )
            slot = fillet(slot.edges().filter_by(Axis.Y), 12.0)
            body -= slot

    return _label(body, label)


def crank_joint_radius(od: float, flange_t: float = 10.0) -> float:
    """Swept radius of a cranked link about the joint it hangs from.

    The parent link's fairing must clear this radius, otherwise the two foul
    as soon as the joint leaves its zero position.
    """
    return (od + flange_t) / 2


def cranked_link(
    top_act: str,
    drop: float,
    od: float,
    wall: float,
    bottom_act: str | None,
    label: str,
    bottom_style: str = "inline",
    turn: str = "x",
):
    """Link driven by a PITCH module: turns 90 deg off the module face.

    Local frame: origin at the driving module's output face, on its axis. The
    link runs down to z = -drop, where the next module seats.
    """
    top = ACTUATORS[top_act]
    flange_t = 10.0
    w = ARM["yoke_wall"]
    spin = Rot(0, 90, 0) if turn == "x" else Rot(-90, 0, 0)

    body = output_hub(top, flange_t, spin)

    off = flange_t / 2
    shift = (off, 0, -drop / 2) if turn == "x" else (0, off, -drop / 2)
    body += Pos(*shift) * Box(od + flange_t, od + flange_t, drop)
    body = _fillet_vertical(body, 9.0)

    # Everything above the joint axis is trimmed back to a cylinder coaxial
    # with that axis: a square shoulder there sweeps a larger circle as the
    # joint moves and fouls the parent link.
    joint_r = crank_joint_radius(od, flange_t)
    big = 4 * (drop + od)
    keep = spin * Cylinder(radius=joint_r, height=big)
    body -= (Pos(0, 0, big / 2 - joint_r) * Box(big, big, big)) - keep
    # Cavity opens at the bottom and stops clear of the hub, so the bolt
    # flange keeps a full ring of material behind it.
    cav_top = -(flange_t + w)
    cav_bot = -drop - 10.0
    # Cavity is centred on the limb axis (not on the hub offset) so equipment
    # mounted inside the link is concentric with the tube.
    cav_w = od + flange_t - 2 * wall - 2 * abs(off)
    body -= Pos(0, 0, (cav_top + cav_bot) / 2) * Box(
        cav_w, cav_w, cav_top - cav_bot
    )

    if bottom_act and bottom_style == "inline":
        bot = ACTUATORS[bottom_act]
        seat = Pos(0, 0, -drop) * _cyl(max(od, bot.housing_od + 14.0), flange_t)
        seat -= Pos(0, 0, -drop) * actuator_interface_cut(bot, flange_t + 1.0)
        seat -= Pos(0, 0, -drop) * _cyl(bot.bore + 3.0, flange_t)
        body += seat

    elif bottom_act and bottom_style in ("pitch", "roll"):
        bot = ACTUATORS[bottom_act]
        along_x = bottom_style == "pitch"
        plate = -(bot.length + 8.0)
        face_rot = Rot(0, 90, 0) if along_x else Rot(-90, 0, 0)
        plate_pos = (plate, 0, -drop) if along_x else (0, plate, -drop)
        neck_pos = (plate / 2, 0, -drop + 12.0) if along_x else (0, plate / 2, -drop + 12.0)
        neck_size = (
            (abs(plate), bot.housing_od + 14.0, 30.0)
            if along_x
            else (bot.housing_od + 14.0, abs(plate), 30.0)
        )
        body += Pos(*plate_pos) * face_rot * _cyl(bot.housing_od + 14.0, 8.0)
        body += Pos(*neck_pos) * Box(*neck_size)
        body -= Pos(*neck_pos) * Box(
            neck_size[0] - (2 * w if along_x else 2 * w),
            neck_size[1] - 2 * w,
            neck_size[2] - 2 * w,
        )
        body -= Pos(*plate_pos) * face_rot * actuator_interface_cut(bot, 9.0)

    return _label(body, label)


def tendon_drive_pack():
    """Four finger drives and their bulkhead, mounted inside the forearm.

    A 96 x 30 mm palm cannot hold four finger actuators, so the drives live in
    the forearm and pull tendons through the wrist - which is why every
    phalanx carries a tendon channel. Laid out 2 x 2 to fit the forearm
    cavity, which is what forced the smaller DSM-30 unit.

    Local frame: forearm tube axis on Z, origin at the pack's top face, the
    pack running down.
    """
    sw, sd, sh = FINGER_SERVO["body"]
    gap = 0.0
    # Matches the cavity cranked_link leaves: the crank box is od + flange_t
    # wide, walls take 2 x link_wall, and the hub offset takes flange_t.
    cavity = ARM["link_od_fore"] - 2 * ARM["link_wall"]
    footprint = (2 * sw + gap, 2 * sd + gap)
    diagonal = (footprint[0] ** 2 + footprint[1] ** 2) ** 0.5
    if diagonal > cavity:
        raise ValueError(
            f"tendon pack {footprint[0]:.0f} x {footprint[1]:.0f} mm (diagonal "
            f"{diagonal:.1f}) does not fit the {cavity:.0f} mm forearm bore"
        )

    parts = []
    for row in range(2):
        for col in range(2):
            x = (col - 0.5) * (sw + gap)
            y = (row - 0.5) * (sd + gap)
            unit = Pos(x, y, -(sh / 2 + 5.0)) * Box(sw, sd, sh)
            unit += Pos(x, y, -5.0) * _cyl(FINGER_SERVO["horn_od"], 5.0, align=TOP)
            parts.append(_label(unit, f"finger_drive_{row * 2 + col + 1}"))

    plate = _cyl(cavity - 4.0, 5.0, align=TOP)
    plate -= _at(
        PolarLocations(14.0, 4, start_angle=45.0), _cyl(5.0, 7.0, align=TOP)
    )  # tendon exits toward the wrist
    parts.append(_label(plate, "tendon_bulkhead"))
    return Compound(label="tendon_drive_pack", children=parts)


# --------------------------------------------------------------------------
# Hand
#
# Knuckles are forks: the fixed side carries two cheeks and the moving side's
# boss runs between them on a 623 bearing each side. Building both sides as a
# single boss on the same axis made them interfere, which the self-collision
# check caught.
# --------------------------------------------------------------------------

FINGER_CHEEK = 4.0  # thickness of one fork cheek
FINGER_GAP = 0.6  # running clearance per side inside a fork


def _fork(width: float, boss_d: float, bore_d: float, inner: float):
    """Two coaxial cheeks on the X axis with ``inner`` clear between them."""
    cheek = _cyl(boss_d, FINGER_CHEEK)
    left = Pos(-(inner / 2 + FINGER_CHEEK), 0, 0) * Rot(0, 90, 0) * cheek
    right = Pos(inner / 2, 0, 0) * Rot(0, 90, 0) * cheek
    body = left + right
    body -= Rot(0, 90, 0) * _cyl(
        bore_d, width + 4, align=(Align.CENTER, Align.CENTER, Align.CENTER)
    )
    return body


def thumb_pad_frame():
    """Frame of the thumb mounting pad's TOP face, in palm-local coordinates.

    Shared by ``palm`` and the assembly so the thumb pivots on the fork the
    palm actually provides rather than floating near it.
    """
    h = HAND
    w = h["palm"][0]
    t = h["palm"][1]
    return (
        Pos(
            w / 2 - h["thumb_pad_inset"],
            t / 2 + h["thumb_pad_proud"],
            -ARM["wrist_to_palm"] - h["thumb_pad_top"],
        )
        * Rot(0, 0, h["thumb_cant"])
    )


def palm(n_fingers: int | None = None):
    """Palm chassis: forked knuckles for three fingers and an opposable thumb.

    Local frame: origin at the wrist-ROLL module's output face, on the roll
    axis (which is on Y). A flange normal to Y bolts to that output; a neck
    drops ``ARM["wrist_to_palm"]`` to the palm top face; the palm body runs a
    further ``HAND["palm"][2]`` down to the knuckle axes.
    """
    h = HAND
    n_fingers = h["n_fingers"] if n_fingers is None else n_fingers
    w, t, l = h["palm"]
    wall = h["palm_wall"]
    neck = ARM["wrist_to_palm"]
    wr = ACTUATORS["A40"]
    b = BEARINGS["623"]

    body = output_hub(wr, 8.0, Rot(-90, 0, 0))
    body += Pos(0, 4.0, -neck / 2) * Box(44.0, t, neck)

    shell = Pos(0, 0, -neck - l) * Box(w, t, l, align=BOTTOM)
    shell = fillet(shell.edges().filter_by(Axis.Z), 6.0)
    shell -= Pos(0, 0, -neck - l + wall) * Box(
        w - 2 * wall, t - 2 * wall, l - 2 * wall, align=BOTTOM
    )
    body += shell

    # Forked knuckles. The finger boss (width finger_w) runs between the two
    # cheeks with FINGER_GAP of clearance per side.
    knuckle_z = -neck - l
    inner = h["finger_w"] + 2 * FINGER_GAP
    span = (n_fingers - 1) * h["finger_pitch"]
    for i in range(n_fingers):
        x = -span / 2 + i * h["finger_pitch"]
        body += Pos(x, 0, knuckle_z) * _fork(
            h["finger_w"], h["knuckle_od"], b.bore, inner
        )

    # Thumb mounting pad, canted for opposition, on the +X edge. Its far end
    # carries a fork exactly like the finger knuckles, and the assembly hangs
    # the thumb off that same datum.
    pad_at = thumb_pad_frame()
    body += pad_at * Box(28.0, t, h["thumb_pad_len"], align=TOP)
    body += pad_at * Pos(0, 0, -h["thumb_pad_len"]) * _fork(
        h["finger_w"], h["knuckle_od"], b.bore, inner
    )

    # Tendon guide holes through the palm floor, one per digit.
    for i in range(n_fingers + 1):
        x = -w / 2 + 14.0 + i * (w - 28.0) / max(n_fingers, 1)
        body -= Pos(x, 0, -neck - 6.0) * _cyl(5.0, l)

    # Swept relief for each knuckle boss. The boss is a cylinder of the
    # phalanx thickness centred ON the palm's bottom face, so without this the
    # upper half of every boss is buried in the shell.
    relief_d = h["finger_t"] + 2 * FINGER_GAP
    for i in range(n_fingers):
        x = -span / 2 + i * h["finger_pitch"]
        body -= Pos(x, 0, knuckle_z) * Rot(0, 90, 0) * _cyl(
            relief_d, inner, align=(Align.CENTER, Align.CENTER, Align.CENTER)
        )
    body -= pad_at * Pos(0, 0, -h["thumb_pad_len"]) * Rot(0, 90, 0) * _cyl(
        relief_d, inner, align=(Align.CENTER, Align.CENTER, Align.CENTER)
    )

    return _label(body, "palm")


def phalanx(length: float, width: float, thickness: float, label: str, tip: bool = False):
    """One finger segment.

    Local frame: pivot axis on X at the origin; the segment runs to z=-length.
    The PROXIMAL end is a plain boss that runs inside the parent's fork; the
    DISTAL end is itself a fork, unless this is the fingertip.
    """
    b = BEARINGS["623"]
    body = Pos(0, 0, -length / 2) * Box(width, thickness, length)
    body = fillet(body.edges().filter_by(Axis.X), 3.0)

    # Proximal boss: runs inside the parent fork.
    body += Rot(0, 90, 0) * _cyl(
        thickness, width, align=(Align.CENTER, Align.CENTER, Align.CENTER)
    )
    body -= Rot(0, 90, 0) * _cyl(
        b.od, width + 2, align=(Align.CENTER, Align.CENTER, Align.CENTER)
    )

    if tip:
        body += Pos(0, 0, -length) * Sphere(radius=thickness / 2)
        # Elastomer grip pad recess on the palmar face.
        body -= Pos(0, thickness / 2, -length / 2) * Box(
            width - 3.0, HAND["pad_thk"], length * 0.7
        )
    else:
        inner = width + 2 * FINGER_GAP
        # Yoke that carries the segment body out to both fork cheeks; without
        # it the cheeks float free of the phalanx.
        body += Pos(0, 0, -length + 9.0) * Box(
            inner + 2 * FINGER_CHEEK, thickness, 18.0
        )
        body += Pos(0, 0, -length) * _fork(width, thickness, b.bore, inner)
        # Waist the segment so the fork cheeks are the widest part.
        body -= Pos(0, 0, -length) * Rot(0, 90, 0) * _cyl(
            thickness + 1.0, inner, align=(Align.CENTER, Align.CENTER, Align.CENTER)
        )

    # Tendon channel down the flexor side.
    body -= Pos(0, thickness / 2 - 3.0, -length / 2) * Rot(90, 0, 0) * _cyl(
        3.0, length, align=(Align.CENTER, Align.CENTER, Align.CENTER)
    )
    return _label(body, label)
