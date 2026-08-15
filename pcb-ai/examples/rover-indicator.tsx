/**
 * Rover board 4 of 4 — status indicator.
 *
 * The smallest board in the stack and the one most likely to be skipped, which is
 * exactly why it is here: when a rover stops moving, the first question is whether the
 * controller is alive, and without an indicator the answer costs a multimeter and a
 * disassembly.
 *
 * Three LEDs off a 4-pin header: power, and two GPIO-driven status lines the firmware
 * can use for whatever it likes. Deliberately dumb — no regulator, no protection, no
 * logic. It runs from the controller's 3V3 and is the last thing that should ever fail.
 *
 * J1  3V3 + GND + 2 signals from the controller
 */
// Sized by tools/autosize.ts rather than by guessing: the content is 18.06 x 10.63 mm,
// so 22 x 15 leaves the fab's 0.3mm copper-to-edge clearance plus 1.5mm for handling.
// The parts also moved 4.5mm right — they had drifted left of centre, and shrinking the
// outline without moving them would have cut J1 off the board rather than saving space.
export default () => (
  <board width="22mm" height="16mm" pcbPack pcbPackGap="1.2mm" layers={2} minViaHoleDiameter="0.3mm" minViaPadDiameter="0.6mm">

    {/* M2 rather than M3: at 22x14 there is no room for an M3 boss and its
        clearance. Two holes, not four — a board this small is held adequately on
        a diagonal, and four would leave nowhere for the LEDs. */}
    <hole name="MH1" diameter="2.2mm" pcbX={-9.1} pcbY={-5.1} />
    <hole name="MH2" diameter="2.2mm" pcbX={-9.1} pcbY={4.9} />
    <hole name="MH3" diameter="2.2mm" pcbX={8.9} pcbY={-2.1} />
    <net name="V3V3" />
    <net name="GND" />
    <net name="STAT1" />
    <net name="STAT2" />

    {/* Header at the west edge, LEDs in a row along the north edge so they read as a
        group when the board is mounted. */}
    {/* x=-8, not -10: a 4-way 2.54mm header is ~10mm long, so at -10 it hung 1.56mm off
        a 26mm board. tscircuit refuses to route when placement is invalid rather than
        producing copper that runs off the edge — the right call, and the reason a
        placement error reads as ten routing errors until you look at the root cause. */}
    <pinheader name="J1" pinCount={4} footprint="pinrow4" pitch="2.54mm"
      pcbX={-3.5} pcbY={0} schX={-10} schY={0} />

    <capacitor name="C1" capacitance="100nF" footprint="0402"
      maxDecouplingTraceLength="12mm" pcbX={0.5} pcbY={-4} schX={-6} schY={-3} />

    {/* ~1.3 mA each at 3V3 through 1k with a 2V forward drop: visible indoors, and
        small enough that three of them do not move the controller's rail. */}
    <resistor name="R1" resistance="1k" footprint="0402" pcbX={3.5} pcbY={4} schX={-2} schY={3} />
    <led name="D1" color="green" footprint="0603" pcbX={7.5} pcbY={4} schX={1} schY={3} />

    <resistor name="R2" resistance="1k" footprint="0402" pcbX={3.5} pcbY={0} schX={-2} schY={0} />
    <led name="D2" color="red" footprint="0603" pcbX={7.5} pcbY={0} schX={1} schY={0} />

    <resistor name="R3" resistance="1k" footprint="0402" pcbX={3.5} pcbY={-4} schX={-2} schY={-3} />
    <led name="D3" color="yellow" footprint="0603" pcbX={7.5} pcbY={-4} schX={1} schY={-3} />

    <trace name="T_J1_V" from=".J1 > .pin1" to="net.V3V3" />
    <trace name="T_J1_G" from=".J1 > .pin2" to="net.GND" />
    <trace name="T_J1_S1" from=".J1 > .pin3" to="net.STAT1" />
    <trace name="T_J1_S2" from=".J1 > .pin4" to="net.STAT2" />

    <trace name="T_C1_P" from=".C1 > .pin1" to="net.V3V3" />
    <trace name="T_C1_N" from=".C1 > .pin2" to="net.GND" />

    {/* power LED: always on whenever the rail is up */}
    <trace name="T_R1_A" from=".R1 > .pin1" to="net.V3V3" />
    <trace name="T_R1_B" from=".R1 > .pin2" to=".D1 > .anode" />
    <trace name="T_D1_K" from=".D1 > .cathode" to="net.GND" />

    {/* two firmware-driven status lines */}
    <trace name="T_R2_A" from=".R2 > .pin1" to="net.STAT1" />
    <trace name="T_R2_B" from=".R2 > .pin2" to=".D2 > .anode" />
    <trace name="T_D2_K" from=".D2 > .cathode" to="net.GND" />

    <trace name="T_R3_A" from=".R3 > .pin1" to="net.STAT2" />
    <trace name="T_R3_B" from=".R3 > .pin2" to=".D3 > .anode" />
    <trace name="T_D3_K" from=".D3 > .cathode" to="net.GND" />
  </board>
)
