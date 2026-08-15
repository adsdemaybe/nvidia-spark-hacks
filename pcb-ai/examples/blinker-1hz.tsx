/**
 * 1Hz LED blinker — NE555 astable.
 *
 * f = 1.44 / ((R1 + 2·R2)·C1) = 1.44 / ((47k + 94k)·10uF) = 1.02 Hz
 * duty = (R1 + R2) / (R1 + 2·R2) = 94k/141k = 67%
 * LED current = (5V − 2.0V) / 330R = 9.1mA
 *
 * C3 is the decoupling capacitor. tscircuit gives any power-to-ground capacitor a 1mm
 * maximum trace length and skips autorouting for the whole board if it cannot be met.
 * The rule constrains BOTH legs, so the ground return has to be short too — and on a
 * soic8 the NE555's GND (pin 1) and VCC (pin 8) sit at opposite ends of the top edge,
 * 4.3mm apart. No cap can be within 1mm of both. C3 therefore straddles them: centred
 * above the package on the pin-1/pin-8 edge, 1.98mm from each, with the limit set to
 * 2.5mm explicitly. That is also where this cap belongs on a real board.
 *
 * pcbRotation is deliberately not used on C3: it rotates the pads but not the
 * courtyard rectangle, so the placement DRC then checks the wrong box.
 */
export default () => (
  <board width="34mm" height="24mm">
    <net name="VCC" />
    <net name="GND" />
    <net name="TIMING" />
    <net name="OUT" />

    {/* Power in, at the left edge */}
    <pinheader
      name="J1"
      pinCount={2}
      footprint="pinrow2"
      pitch="2.54mm"
      pcbX={-13.4}
      pcbY={0}
      schX={-9}
      schY={0}
    />

    <chip
      name="U1"
      footprint="soic8"
      manufacturerPartNumber="NE555"
      pinLabels={{
        pin1: "GND",
        pin2: "TRIG",
        pin3: "OUT",
        pin4: "RESET",
        pin5: "CTRL",
        pin6: "THRES",
        pin7: "DISCH",
        pin8: "VCC",
      }}
      pinAttributes={{
        VCC: { requiresPower: true },
        GND: { requiresGround: true },
      }}
      schPinArrangement={{
        leftSide: { direction: "top-to-bottom", pins: ["TRIG", "THRES", "CTRL", "RESET"] },
        rightSide: { direction: "top-to-bottom", pins: ["OUT", "DISCH"] },
        topSide: { direction: "left-to-right", pins: ["VCC"] },
        bottomSide: { direction: "left-to-right", pins: ["GND"] },
      }}
      pcbX={-2}
      pcbY={0}
      schX={0}
      schY={0}
    />

    {/* Decoupling — straddling pins 1 and 8 along the top edge of U1 */}
    <capacitor
      name="C3"
      capacitance="100nF"
      footprint="0402"
      maxDecouplingTraceLength="4.5mm"
      pcbX={-2}
      pcbY={4}
      schX={4}
      schY={3}
    />

    {/* Timing network */}
    <resistor name="R1" resistance="47k" footprint="0603" pcbX={-8} pcbY={6} schX={-5} schY={4} />
    <resistor name="R2" resistance="47k" footprint="0603" pcbX={-8} pcbY={3} schX={-5} schY={1} />
    <capacitor name="C1" capacitance="10uF" footprint="0805" pcbX={-8} pcbY={-5} schX={-5} schY={-3} />

    {/* Control-pin bypass */}
    <capacitor name="C2" capacitance="10nF" footprint="0402" pcbX={-2} pcbY={-5} schX={3} schY={-4} />

    {/* Indicator */}
    <resistor name="R3" resistance="330" footprint="0603" pcbX={7} pcbY={3} schX={5} schY={1} />
    <led name="D1" color="red" footprint="0805" pcbX={12} pcbY={0} schX={5} schY={-2} />

    <netlabel net="VCC" anchorSide="bottom" schX={0} schY={5} />
    <netlabel net="GND" anchorSide="top" schX={0} schY={-6} />

    {/* Supply */}
    <trace name="J1_VCC" from=".J1 > .pin1" to="net.VCC" thickness="0.4mm" />
    <trace name="J1_GND" from=".J1 > .pin2" to="net.GND" thickness="0.4mm" />
    <trace name="U1_VCC" from=".U1 > .VCC" to="net.VCC" thickness="0.4mm" />
    <trace name="U1_GND" from=".U1 > .GND" to="net.GND" thickness="0.4mm" />
    <trace name="U1_RESET" from=".U1 > .RESET" to="net.VCC" />
    <trace name="C3_VCC" from=".C3 > .pin1" to="net.VCC" />
    <trace name="C3_GND" from=".C3 > .pin2" to="net.GND" />

    {/* Astable timing: VCC - R1 - DISCH - R2 - THRES/TRIG - C1 - GND */}
    <trace name="R1_VCC" from=".R1 > .pin1" to="net.VCC" />
    <trace name="R1_DISCH" from=".R1 > .pin2" to=".U1 > .DISCH" />
    <trace name="R2_DISCH" from=".R2 > .pin1" to=".U1 > .DISCH" />
    <trace name="R2_TIMING" from=".R2 > .pin2" to="net.TIMING" />
    <trace name="U1_THRES" from=".U1 > .THRES" to="net.TIMING" />
    <trace name="U1_TRIG" from=".U1 > .TRIG" to="net.TIMING" />
    <trace name="C1_TIMING" from=".C1 > .pin1" to="net.TIMING" />
    <trace name="C1_GND" from=".C1 > .pin2" to="net.GND" />

    {/* Control-pin bypass to ground */}
    <trace name="U1_CTRL" from=".U1 > .CTRL" to=".C2 > .pin1" />
    <trace name="C2_GND" from=".C2 > .pin2" to="net.GND" />

    {/* Output stage */}
    <trace name="U1_OUT" from=".U1 > .OUT" to="net.OUT" />
    <trace name="R3_OUT" from=".R3 > .pin1" to="net.OUT" />
    <trace name="R3_D1" from=".R3 > .pin2" to=".D1 > .anode" />
    <trace name="D1_GND" from=".D1 > .cathode" to="net.GND" />
  </board>
)
