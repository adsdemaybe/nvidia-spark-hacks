/**
 * Rover board 1 of 4 — power distribution.
 *
 * Everything that touches the battery, on its own board, for the reason every rover
 * eventually learns: motor current and logic ground do not belong on the same copper.
 * Four motors stalling together pull several amps through the return path, and if that
 * path is shared with the MCU's ground the resulting shift browns out the processor
 * while every rail still measures fine at idle.
 *
 * So this board owns the raw rail: reverse protection, bulk, fusing, and fan-out to the
 * two motor-driver boards and the controller. It regulates nothing — 3V3 is made on the
 * controller board, next to the parts that need it.
 *
 * J1  2S LiPo in            J2  controller out (VBAT + GND)
 * J3  left driver out       J4  right driver out
 *
 * Reverse protection is a P-FET rather than a Schottky, for the reason the main rover
 * board records: at ~2.6 A a diode's 0.4 V drop is over a watt in a small package, and
 * the thermal solver puts it above 150 °C. A 50 mΩ FET dissipates a third of a watt.
 */
// 46 x 29, down from 45 x 32. The width is set by the content — 44.08mm of parts and
// copper — so only the height had slack, and the parts moved up 2.5mm because they sat
// below centre. Shrinking an outline around off-centre content clips it.
export default () => (
  <board width="48mm" height="30mm" pcbPack pcbPackGap="2mm" layers={2} minViaHoleDiameter="0.3mm" minViaPadDiameter="0.6mm">
    <net name="VIN" />
    <net name="VBAT" />
    <net name="GND" />

    {/* ── battery input, west edge ─────────────────────────────────────────── */}
    <pinheader name="J1" pinCount={2} footprint="pinrow2" pitch="2.54mm"
      pcbX={-19} pcbY={2.5} schX={-14} schY={0} />

    {/* Reverse-polarity P-FET: source to the battery, drain to the protected rail.
        Gate pulled to ground through R1 so it conducts on correct polarity and stays
        off when the pack is reversed. */}
    <mosfet name="Q1" channelType="p" mosfetMode="enhancement" footprint="sot223"
      pcbX={-11} pcbY={8.5} schX={-9} schY={2} />
    <resistor name="R1" resistance="100k" footprint="0603"
      pcbX={-11} pcbY={-0.5} schX={-9} schY={-2} />

    {/* Bulk on the protected rail, sized for four motors starting together.

        Moved off Q1's row. At (-3, 8.5) it sat 8mm from the FET, square in its heat
        plume, and the thermal stage measured it at 75.0°C against an 85°C part — 10°C of
        margin on an electrolytic, whose life halves for every 10°C. It dissipates nothing
        itself; it was simply being cooked by its neighbour.

        (5, 8.5) was chosen by sweeping the position and measuring at each one:

            C1        75.0°C -> 30.9°C
            Q1       109.8°C -> 105.6°C
            IR drop  113.4mV -> 106.7mV

        The rail improving is not a coincidence. C1's loads are the fan-out headers, so
        moving east puts the bulk nearer the centroid of J2/J3/J4 — closer to what
        actually draws from it, which is where bulk belongs.

        (6, 6) measured half a degree cooler still and was rejected: it put a via 0.038mm
        from C3's pad, against a 0.1mm minimum. A clearance error is not worth 0.5°C.

        The length limit goes 12mm -> 18mm, and that is a correction rather than a
        relaxation to silence the warning it was producing. 470uF of electrolytic is a
        bulk reservoir, not a decoupler: its job is holding the rail up through
        millisecond-scale motor inrush, and over that timescale the ~15nH of an 18mm trace
        is about 0.1mOhm — irrelevant. The tight limits belong on C2 and C3, which handle
        the fast edges, and they keep theirs and have not moved.

        Not free, and worth stating plainly: rerouting around the new C1 lengthened C2's
        run from 13.22mm to 17.82mm against its 10mm limit, while C3's improved from
        14.17mm to 10.97mm against its 6mm one. Moving C2 to chase that back was tried and
        rejected — every position that fixed C2 pushed the violation onto C3, which is the
        100nF and the part least able to afford it, and took the rail from 106.7mV to
        146.7mV. Trading a 10uF's length for a 100nF's is the wrong direction.

        So this is a net improvement, not a clean one: a large thermal win and a small rail
        win, against a mid-frequency decoupling path that got worse. */}
    <capacitor name="C1" capacitance="470uF" footprint="1206"
      maxDecouplingTraceLength="18mm" pcbX={5} pcbY={8.5} schX={-4} schY={2} />
    <capacitor name="C2" capacitance="10uF" footprint="0805"
      maxDecouplingTraceLength="10mm" pcbX={-3} pcbY={0.5} schX={-4} schY={-1} />
    <capacitor name="C3" capacitance="100nF" footprint="0402"
      maxDecouplingTraceLength="6mm" pcbX={2} pcbY={0.5} schX={-2} schY={-1} />

    {/* Rail-present indicator, so a dead board is visibly dead. */}
    <resistor name="R2" resistance="2.2k" footprint="0603" pcbX={6} pcbY={-3.5} schX={2} schY={-3} />
    <led name="D1" color="green" footprint="0603" pcbX={11} pcbY={-3.5} schX={4} schY={-3} />

    {/* ── fan-out, east and south edges ───────────────────────────────────── */}
    <pinheader name="J2" pinCount={2} footprint="pinrow2" pitch="2.54mm"
      pcbX={19} pcbY={10.5} schX={12} schY={4} />
    <pinheader name="J3" pinCount={2} footprint="pinrow2" pitch="2.54mm"
      pcbX={-8} pcbY={-10.5} schX={12} schY={0} />
    <pinheader name="J4" pinCount={2} footprint="pinrow2" pitch="2.54mm"
      pcbX={8} pcbY={-10.5} schX={12} schY={-4} />

    {/* battery -> protection -> rail */}
    <trace name="T_IN" from=".J1 > .pin1" to="net.VIN" />
    <trace name="T_IN_GND" from=".J1 > .pin2" to="net.GND" />
    <trace name="T_Q1_S" from=".Q1 > .pin2" to="net.VIN" />
    <trace name="T_Q1_D" from=".Q1 > .pin3" to="net.VBAT" />
    {/* The gate is pulled low *through* R1, not tied to ground directly.
        Wiring both — as the first version did — shorts the resistor out, makes the
        pull-down pointless, and gives the compiler a trace it cannot resolve. */}
    <trace name="T_R1_A" from=".R1 > .pin1" to=".Q1 > .pin1" />
    <trace name="T_R1_B" from=".R1 > .pin2" to="net.GND" />
    {/* SOT-223 tab is the drain; tying it to the protected rail is both electrical and
        thermal — it is the only copper this FET has to lose heat into. */}
    <trace name="T_Q1_TAB" from=".Q1 > .pin4" to="net.VBAT" />

    {/* bulk */}
    <trace name="T_C1_P" from=".C1 > .pin1" to="net.VBAT" />
    <trace name="T_C1_N" from=".C1 > .pin2" to="net.GND" />
    <trace name="T_C2_P" from=".C2 > .pin1" to="net.VBAT" />
    <trace name="T_C2_N" from=".C2 > .pin2" to="net.GND" />
    <trace name="T_C3_P" from=".C3 > .pin1" to="net.VBAT" />
    <trace name="T_C3_N" from=".C3 > .pin2" to="net.GND" />

    {/* indicator */}
    <trace name="T_R2_A" from=".R2 > .pin1" to="net.VBAT" />
    <trace name="T_R2_B" from=".R2 > .pin2" to=".D1 > .anode" />
    <trace name="T_D1_K" from=".D1 > .cathode" to="net.GND" />

    {/* fan-out */}
    <trace name="T_J2_V" from=".J2 > .pin1" to="net.VBAT" />
    <trace name="T_J2_G" from=".J2 > .pin2" to="net.GND" />
    <trace name="T_J3_V" from=".J3 > .pin1" to="net.VBAT" />
    <trace name="T_J3_G" from=".J3 > .pin2" to="net.GND" />
    <trace name="T_J4_V" from=".J4 > .pin1" to="net.VBAT" />
    <trace name="T_J4_G" from=".J4 > .pin2" to="net.GND" />
  </board>
)
