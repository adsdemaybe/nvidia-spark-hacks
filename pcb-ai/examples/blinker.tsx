export default () => (
  <board width="30mm" height="24mm">
    <net name="VCC" />
    <net name="GND" />

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
      schPinArrangement={{
        leftSide: { direction: "top-to-bottom", pins: ["TRIG", "THRES", "CTRL", "RESET"] },
        rightSide: { direction: "top-to-bottom", pins: ["OUT", "DISCH"] },
        topSide: { direction: "left-to-right", pins: ["VCC"] },
        bottomSide: { direction: "left-to-right", pins: ["GND"] },
      }}
      pcbX={0}
      pcbY={0}
      schX={0}
      schY={0}
    />

    <resistor name="R1" resistance="10k" footprint="0402" pcbX={-8} pcbY={5} schX={-5} schY={4} />
    <resistor name="R2" resistance="47k" footprint="0402" pcbX={-8} pcbY={2} schX={-5} schY={1} />
    <capacitor name="C1" capacitance="10uF" footprint="0603" pcbX={-8} pcbY={-3} schX={-5} schY={-3} />
    <capacitor name="C2" capacitance="100nF" footprint="0402" pcbX={0} pcbY={-5} schX={3} schY={-3} />
    <resistor name="R3" resistance="330" footprint="0402" pcbX={8} pcbY={2} schX={5} schY={1} />
    <led name="D1" color="red" footprint="0603" pcbX={8} pcbY={-2} schX={5} schY={-2} />
    <pinheader name="J1" pinCount={2} footprint="pinrow2" pitch="2.54mm" pcbX={-12} pcbY={-8} schX={-9} schY={0} />

    <netlabel net="VCC" anchorSide="bottom" schX={0} schY={5} />
    <netlabel net="GND" anchorSide="top" schX={0} schY={-6} />

    <trace from=".J1 > .pin1" to="net.VCC" />
    <trace from=".J1 > .pin2" to="net.GND" />

    <trace from=".U1 > .VCC" to="net.VCC" />
    <trace from=".U1 > .GND" to="net.GND" />
    <trace from=".U1 > .RESET" to="net.VCC" />

    <trace from=".R1 > .pin1" to="net.VCC" />
    <trace from=".R1 > .pin2" to=".U1 > .DISCH" />
    <trace from=".R2 > .pin1" to=".U1 > .DISCH" />
    <trace from=".R2 > .pin2" to=".U1 > .THRES" />
    <trace from=".U1 > .TRIG" to=".U1 > .THRES" />
    <trace from=".C1 > .pos" to=".U1 > .THRES" />
    <trace from=".C1 > .neg" to="net.GND" />

    <trace from=".U1 > .CTRL" to=".C2 > .pin1" />
    <trace from=".C2 > .pin2" to="net.GND" />

    <trace from=".U1 > .OUT" to=".R3 > .pin1" />
    <trace from=".R3 > .pin2" to=".D1 > .anode" />
    <trace from=".D1 > .cathode" to="net.GND" />
  </board>
)
