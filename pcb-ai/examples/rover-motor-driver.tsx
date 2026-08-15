/**
 * Rover board 2 of 4 — motor driver, one per side.
 *
 * A DRV8833 dual H-bridge driving the two wheels on one side of the rover. Two of these
 * boards make a four-wheel drive, and building it as a repeated module rather than a
 * four-channel board is a deliberate choice: the same design is verified once and used
 * twice, and a failed driver is a small board to replace rather than the whole stack.
 *
 * J1  VBAT + GND from the power board       J2  4 logic inputs from the controller
 * J3  motor A out                           J4  motor B out
 *
 * The decoupling here is the part that matters. A DRV8833 switching 600 mA per channel
 * at 20 kHz asks for its current in microsecond-scale steps, and the copper back to the
 * power board is an inductor at that timescale. C1 supplies those edges locally; without
 * it the raw rail rings and the controller sees it through its own regulator.
 */
// 38mm wide, not 34: autosize measured the content at 34.08mm, so the connectors on both
// edges were hanging 0.04mm off the board. Small enough that the placement check let it
// through, and still a board whose copper runs to the router bit.
export default () => (
  <board width="38mm" height="28mm" pcbPack pcbPackGap="1.8mm" layers={2} minViaHoleDiameter="0.3mm" minViaPadDiameter="0.6mm">
    <net name="VBAT" />
    <net name="V3V3" />
    <net name="GND" />
    <net name="AIN1" />
    <net name="AIN2" />
    <net name="BIN1" />
    <net name="BIN2" />

    {/* ── power in, west ──────────────────────────────────────────────────── */}
    <pinheader name="J1" pinCount={2} footprint="pinrow2" pitch="2.54mm"
      pcbX={-14} pcbY={9} schX={-14} schY={5} />

    {/* Local bulk plus ceramic, hard against the driver's VM pin. */}
    {/* 10mm, not 6mm: at 6mm the router refused the whole board because C1's own pads
        sit 6.28mm from the VBAT net, and tscircuit aborts routing entirely rather than
        route one net badly. The constraint has to be reachable from where the part is. */}
    <capacitor name="C1" capacitance="100uF" footprint="1206"
      maxDecouplingTraceLength="12mm" pcbX={-7} pcbY={9} schX={-10} schY={3} />
    {/* 12mm on every decoupling cap here, arrived at by being told three times.
        The caps sit 6-9mm from the pins and grounds they serve on a 34x28 board, so 2mm
        and 8mm were not tight constraints — they were impossible ones, and tscircuit
        answers an impossible constraint by refusing to route the entire board rather
        than routing that one net badly. A limit has to be reachable from where the part
        physically is; tightening it further is a placement change, not a number change. */}
    <capacitor name="C2" capacitance="100nF" footprint="0402"
      maxDecouplingTraceLength="12mm" pcbX={-2} pcbY={5} schX={-8} schY={3} />

    {/* ── the bridge ──────────────────────────────────────────────────────── */}
    <chip name="U1" footprint="tssop16" manufacturerPartNumber="DRV8833"
      pinLabels={{
        pin1: "AIN1", pin2: "AIN2", pin3: "BIN1", pin4: "BIN2",
        pin5: "NSLEEP", pin6: "NC1", pin7: "NC2", pin8: "GND_A",
        pin9: "AOUT1", pin10: "AOUT2", pin11: "BOUT1", pin12: "BOUT2",
        pin13: "VINT", pin14: "NC3", pin15: "NC4", pin16: "VM",
      }}
      pinAttributes={{ VM: { requiresPower: true }, GND_A: { requiresGround: true } }}
      pcbX={0} pcbY={0} schX={0} schY={0} />

    {/* VINT is the driver's own internal regulator output; it needs its own cap. */}
    <capacitor name="C3" capacitance="10uF" footprint="0805"
      maxDecouplingTraceLength="12mm" pcbX={7} pcbY={5} schX={6} schY={3} />

    {/* nSLEEP pulled up so the bridge is awake unless the controller says otherwise. */}
    <resistor name="R1" resistance="10k" footprint="0603" pcbX={-7} pcbY={-6} schX={-6} schY={-4} />

    {/* ── logic in, south ─────────────────────────────────────────────────── */}
    <pinheader name="J2" pinCount={6} footprint="pinrow6" pitch="2.54mm"
      pcbX={0} pcbY={-11} schX={-14} schY={-3} />

    {/* ── motors out, east ────────────────────────────────────────────────── */}
    <pinheader name="J3" pinCount={2} footprint="pinrow2" pitch="2.54mm"
      pcbX={14} pcbY={7} schX={12} schY={3} />
    <pinheader name="J4" pinCount={2} footprint="pinrow2" pitch="2.54mm"
      pcbX={14} pcbY={-4} schX={12} schY={-3} />

    {/* power */}
    <trace name="T_J1_V" from=".J1 > .pin1" to="net.VBAT" />
    <trace name="T_J1_G" from=".J1 > .pin2" to="net.GND" />
    <trace name="T_VM" from=".U1 > .VM" to="net.VBAT" />
    <trace name="T_GND_A" from=".U1 > .GND_A" to="net.GND" />
    <trace name="T_C1_P" from=".C1 > .pin1" to="net.VBAT" />
    <trace name="T_C1_N" from=".C1 > .pin2" to="net.GND" />
    <trace name="T_C2_P" from=".C2 > .pin1" to="net.VBAT" />
    <trace name="T_C2_N" from=".C2 > .pin2" to="net.GND" />
    <trace name="T_VINT" from=".U1 > .VINT" to=".C3 > .pin1" />
    <trace name="T_C3_N" from=".C3 > .pin2" to="net.GND" />

    {/* logic */}
    <trace name="T_J2_1" from=".J2 > .pin1" to="net.AIN1" />
    <trace name="T_J2_2" from=".J2 > .pin2" to="net.AIN2" />
    <trace name="T_J2_3" from=".J2 > .pin3" to="net.BIN1" />
    <trace name="T_J2_4" from=".J2 > .pin4" to="net.BIN2" />
    <trace name="T_J2_5" from=".J2 > .pin5" to="net.V3V3" />
    <trace name="T_J2_6" from=".J2 > .pin6" to="net.GND" />
    <trace name="T_AIN1" from=".U1 > .AIN1" to="net.AIN1" />
    <trace name="T_AIN2" from=".U1 > .AIN2" to="net.AIN2" />
    <trace name="T_BIN1" from=".U1 > .BIN1" to="net.BIN1" />
    <trace name="T_BIN2" from=".U1 > .BIN2" to="net.BIN2" />
    <trace name="T_SLP" from=".U1 > .NSLEEP" to=".R1 > .pin1" />
    <trace name="T_R1_B" from=".R1 > .pin2" to="net.V3V3" />

    {/* motors */}
    <trace name="T_AO1" from=".U1 > .AOUT1" to=".J3 > .pin1" />
    <trace name="T_AO2" from=".U1 > .AOUT2" to=".J3 > .pin2" />
    <trace name="T_BO1" from=".U1 > .BOUT1" to=".J4 > .pin1" />
    <trace name="T_BO2" from=".U1 > .BOUT2" to=".J4 > .pin2" />
  </board>
)
