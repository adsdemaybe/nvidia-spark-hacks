/**
 * 4-motor rover controller — STM32F103 + MPU-6050 gyro + GPS module + 2x DRV8833.
 *
 * Power:   VBAT 7.4V (2S LiPo) -> reverse-protection Schottky -> bulk -> AMS1117-3.3
 *          Motors run direct from VBAT; only logic is regulated.
 *          Logic load ~85mA at 3.3V, so the LDO dissipates (7.4-3.3)*0.085 = 0.35W.
 *          That is the hottest thing on the board and the thermal solver should say so.
 *
 * Motors:  2x DRV8833 dual H-bridge, one per side. 4 PWM pairs from TIM2/TIM3.
 * Sensors: MPU-6050 on I2C1 (PB6/PB7), GPS module on USART1 (PA9/PA10).
 *
 * Every decoupling capacitor carries an explicit maxDecouplingTraceLength. tscircuit
 * silently applies a 1mm limit to any power-to-ground capacitor and aborts autorouting
 * for the entire board if it cannot be met — and on fine-pitch packages the power and
 * ground pins are far enough apart that 1mm is not physically reachable for both legs.
 * Stating the real constraint is better than letting the route abort.
 */
export default () => (
  <board width="72mm" height="46mm" layers={4} pcbPack pcbPackGap="2.5mm">
    <net name="VBAT" />
    <net name="VIN" />
    <net name="V3V3" />
    <net name="GND" />
    <net name="SDA" />
    <net name="SCL" />
    <net name="NRST" />

    {/* ── Power input and regulation ─────────────────────────────────────── */}

    <pinheader name="J1" pinCount={2} footprint="pinrow2" pitch="2.54mm" schX={-16} schY={8} />

    {/* Reverse-polarity protection: P-FET, not a Schottky. At 1.29A a diode's 0.4V drop
        is 0.52W in a SOD-123 — the thermal solver put that at 165C and cooked J1 and C1
        next to it. A 50mohm P-FET dissipates 0.083W for the same function. */}
    <mosfet name="Q1" channelType="p" mosfetMode="enhancement" footprint="sot223" schX={-13} schY={8} />
    <resistor name="R4" resistance="100k" footprint="0402" schX={-13} schY={5} />

    {/* Bulk on the raw rail, sized for motor inrush */}
    <capacitor name="C1" capacitance="220uF" footprint="1206"
      maxDecouplingTraceLength="15mm" schX={-11} schY={6} />

    <chip name="U1" footprint="sot223" manufacturerPartNumber="AMS1117-3.3"
      pinLabels={{ pin1: "GND", pin2: "VOUT", pin3: "VIN", pin4: "TAB" }}
      pinAttributes={{ VIN: { requiresPower: true }, GND: { requiresGround: true },
        VOUT: { providesPower: true } }} schX={-8} schY={7} />

    <capacitor name="C2" capacitance="10uF" footprint="0805"
      maxDecouplingTraceLength="15mm" schX={-10} schY={5} />
    <capacitor name="C3" capacitance="22uF" footprint="0805"
      maxDecouplingTraceLength="15mm" schX={-5} schY={5} />

    {/* ── Microcontroller ────────────────────────────────────────────────── */}

    <chip name="U2" footprint="lqfp48" manufacturerPartNumber="STM32F103C8T6"
      // Real STM32F103C8T6 LQFP48 pinout. Only the pins this board uses are named;
      // the rest keep their pin numbers.
      pinLabels={{
        pin1: "VBAT_MCU",
        pin5: "OSC_IN", pin6: "OSC_OUT", pin7: "NRST",
        pin8: "VSSA", pin9: "VDDA",
        pin10: "PA0_AIN1", pin11: "PA1_AIN2", pin12: "PA2_BIN1", pin13: "PA3_BIN2",
        pin14: "PA4_CIN1", pin15: "PA5_CIN2",
        pin18: "PB0_DIN1", pin19: "PB1_DIN2",
        pin23: "VSS_1", pin24: "VDD_1",
        pin30: "PA9_TX", pin31: "PA10_RX",
        pin34: "SWDIO", pin35: "VSS_2", pin36: "VDD_2", pin37: "SWCLK",
        pin40: "PB4_LED",
        pin42: "PB6_SCL", pin43: "PB7_SDA",
        pin47: "VSS_3", pin48: "VDD_3",
      }}
      pinAttributes={{
        VDD_1: { requiresPower: true }, VDD_2: { requiresPower: true },
        VDD_3: { requiresPower: true }, VSS_1: { requiresGround: true },
        VSS_2: { requiresGround: true }, VSS_3: { requiresGround: true },
      }} schX={0} schY={0} />

    {/* One decoupling capacitor per VDD/VSS pair, placed against its own pin pair */}
    <capacitor name="C4" capacitance="100nF" footprint="0402"
      maxDecouplingTraceLength="15mm" schX={5} schY={6} />
    <capacitor name="C5" capacitance="100nF" footprint="0402"
      maxDecouplingTraceLength="15mm" schX={7} schY={6} />
    <capacitor name="C6" capacitance="100nF" footprint="0402"
      maxDecouplingTraceLength="15mm" schX={9} schY={6} />
    <capacitor name="C7" capacitance="10uF" footprint="0805"
      maxDecouplingTraceLength="15mm" schX={3} schY={6} />

    {/* Analogue supply filter */}
    <capacitor name="C8" capacitance="100nF" footprint="0402"
      maxDecouplingTraceLength="15mm" schX={-3} schY={6} />

    {/* 8MHz crystal */}
    <crystal name="Y1" frequency="8MHz" loadCapacitance="20pF" footprint="0805" maxTraceLength="30mm" schX={-6} schY={-2} />
    <capacitor name="C9" capacitance="22pF" footprint="0402" schX={-8} schY={-1} />
    <capacitor name="C10" capacitance="22pF" footprint="0402" schX={-8} schY={-3} />

    {/* Reset */}
    <pushbutton name="SW1" footprint="pushbutton" schX={-12} schY={-4} />
    <resistor name="R1" resistance="10k" footprint="0402" schX={-12} schY={-1} />

    {/* SWD programming header */}
    <pinheader name="J2" pinCount={4} footprint="pinrow4" pitch="2.54mm" schX={-16} schY={-6} />

    {/* ── Sensors ────────────────────────────────────────────────────────── */}

    <chip name="U3" footprint="qfn24" manufacturerPartNumber="MPU-6050"
      pinLabels={{ pin1: "CLKIN", pin8: "VDD", pin9: "GND_1", pin10: "GND_2",
        pin13: "VLOGIC", pin23: "SCL", pin24: "SDA", pin18: "GND_3" }}
      pinAttributes={{ VDD: { requiresPower: true }, GND_1: { requiresGround: true } }} schX={12} schY={2} />
    <capacitor name="C11" capacitance="100nF" footprint="0402"
      maxDecouplingTraceLength="15mm" schX={15} schY={3} />

    {/* GPS receiver module on a 4-pin header: VCC, GND, TX, RX */}
    <pinheader name="J3" pinCount={4} footprint="pinrow4" pitch="2.54mm" schX={16} schY={2} />

    {/* ── Motor drivers ──────────────────────────────────────────────────── */}

    <chip name="U4" footprint="tssop16" manufacturerPartNumber="DRV8833"
      pinLabels={{ pin1: "AIN1", pin2: "AIN2", pin3: "BIN1", pin4: "BIN2",
        pin5: "NSLEEP", pin8: "GND_A", pin9: "AOUT1", pin10: "AOUT2",
        pin11: "BOUT1", pin12: "BOUT2", pin13: "VINT", pin16: "VM" }}
      pinAttributes={{ VM: { requiresPower: true }, GND_A: { requiresGround: true } }} schX={-8} schY={-10} />
    <capacitor name="C12" capacitance="10uF" footprint="0805"
      maxDecouplingTraceLength="15mm" schX={-11} schY={-9} />
    <capacitor name="C14" capacitance="10uF" footprint="0805"
      maxDecouplingTraceLength="15mm" schX={-11} schY={-12} />

    <chip name="U5" footprint="tssop16" manufacturerPartNumber="DRV8833"
      pinLabels={{ pin1: "AIN1", pin2: "AIN2", pin3: "BIN1", pin4: "BIN2",
        pin5: "NSLEEP", pin8: "GND_A", pin9: "AOUT1", pin10: "AOUT2",
        pin11: "BOUT1", pin12: "BOUT2", pin13: "VINT", pin16: "VM" }}
      pinAttributes={{ VM: { requiresPower: true }, GND_A: { requiresGround: true } }} schX={8} schY={-10} />
    <capacitor name="C13" capacitance="10uF" footprint="0805"
      maxDecouplingTraceLength="15mm" schX={11} schY={-9} />
    <capacitor name="C15" capacitance="10uF" footprint="0805"
      maxDecouplingTraceLength="15mm" schX={11} schY={-12} />

    {/* Motor output terminals, one pair per motor, at the board edges */}
    <pinheader name="J4" pinCount={2} footprint="pinrow2" pitch="2.54mm" schX={-14} schY={-14} />
    <pinheader name="J5" pinCount={2} footprint="pinrow2" pitch="2.54mm" schX={-10} schY={-14} />
    <pinheader name="J6" pinCount={2} footprint="pinrow2" pitch="2.54mm" schX={10} schY={-14} />
    <pinheader name="J7" pinCount={2} footprint="pinrow2" pitch="2.54mm" schX={14} schY={-14} />

    {/* ── Indicators ─────────────────────────────────────────────────────── */}

    <resistor name="R2" resistance="1k" footprint="0402" schX={13} schY={-4} />
    <led name="D2" color="green" footprint="0603" schX={13} schY={-6} />
    <resistor name="R3" resistance="1k" footprint="0402" schX={16} schY={-4} />
    <led name="D3" color="blue" footprint="0603" schX={16} schY={-6} />

    {/* 4-layer stackup: signals on the outer layers, solid planes inside.
        A ground plane is what makes a board this dense routable — it gives every
        return current a path straight down through a via instead of a long trace
        threaded between 0.5mm-pitch pads. It also flattens the IR drop and spreads
        heat, both of which show up in the analysis. */}
    <copperpour name="GNDPLANE" layer="inner1" connectsTo="net.GND" boardEdgeMargin="0.5mm" />
    <copperpour name="PWRPLANE" layer="inner2" connectsTo="net.V3V3" boardEdgeMargin="0.5mm" />

    <netlabel net="V3V3" anchorSide="bottom" schX={0} schY={10} />
    <netlabel net="GND" anchorSide="top" schX={0} schY={-16} />

    {/* ── Power tree ─────────────────────────────────────────────────────── */}
    <trace name="p_in" from=".J1 > .pin1" to=".Q1 > .source" thickness="1mm" />
    <trace name="p_gnd" from=".J1 > .pin2" to="net.GND" thickness="0.6mm" />
    <trace name="p_vbat" from=".Q1 > .drain" to="net.VBAT" thickness="1mm" />
    <trace name="p_gate" from=".Q1 > .gate" to="net.GND" />
    <trace name="p_gate_r" from=".R4 > .pin1" to=".Q1 > .gate" />
    <trace name="p_gate_r2" from=".R4 > .pin2" to=".J1 > .pin1" />
    <trace name="p_bulk" from=".C1 > .pin1" to="net.VBAT" thickness="1mm" />
    <trace name="p_bulk_g" from=".C1 > .pin2" to="net.GND" thickness="0.6mm" />
    <trace name="p_ldo_in" from=".U1 > .VIN" to="net.VBAT" thickness="1mm" />
    <trace name="p_ldo_gnd" from=".U1 > .GND" to="net.GND" thickness="0.6mm" />
    <trace name="p_ldo_out" from=".U1 > .VOUT" to="net.V3V3" thickness="0.6mm" />
    <trace name="p_ldo_tab" from=".U1 > .TAB" to="net.V3V3" thickness="0.6mm" />
    <trace name="p_c2" from=".C2 > .pin1" to="net.VBAT" />
    <trace name="p_c2g" from=".C2 > .pin2" to="net.GND" />
    <trace name="p_c3" from=".C3 > .pin1" to="net.V3V3" />
    <trace name="p_c3g" from=".C3 > .pin2" to="net.GND" />

    {/* ── MCU supplies ───────────────────────────────────────────────────── */}
    <trace name="m_vdd1" from=".U2 > .VDD_1" to="net.V3V3" />
    <trace name="m_vdd2" from=".U2 > .VDD_2" to="net.V3V3" />
    <trace name="m_vdd3" from=".U2 > .VDD_3" to="net.V3V3" />
    <trace name="m_vdda" from=".U2 > .VDDA" to="net.V3V3" />
    <trace name="m_vbat" from=".U2 > .VBAT_MCU" to="net.V3V3" />
    <trace name="m_vss1" from=".U2 > .VSS_1" to="net.GND" />
    <trace name="m_vss2" from=".U2 > .VSS_2" to="net.GND" />
    <trace name="m_vss3" from=".U2 > .VSS_3" to="net.GND" />
    <trace name="m_vssa" from=".U2 > .VSSA" to="net.GND" />
    <trace name="m_c4" from=".C4 > .pin1" to="net.V3V3" />
    <trace name="m_c4g" from=".C4 > .pin2" to="net.GND" />
    <trace name="m_c5" from=".C5 > .pin1" to="net.V3V3" />
    <trace name="m_c5g" from=".C5 > .pin2" to="net.GND" />
    <trace name="m_c6" from=".C6 > .pin1" to="net.V3V3" />
    <trace name="m_c6g" from=".C6 > .pin2" to="net.GND" />
    <trace name="m_c7" from=".C7 > .pin1" to="net.V3V3" />
    <trace name="m_c7g" from=".C7 > .pin2" to="net.GND" />
    <trace name="m_c8" from=".C8 > .pin1" to="net.V3V3" />
    <trace name="m_c8g" from=".C8 > .pin2" to="net.GND" />

    {/* Clock */}
    <trace name="x_in" from=".U2 > .OSC_IN" to=".Y1 > .pin1" />
    <trace name="x_out" from=".U2 > .OSC_OUT" to=".Y1 > .pin2" />
    <trace name="x_c9" from=".C9 > .pin1" to=".Y1 > .pin1" />
    <trace name="x_c9g" from=".C9 > .pin2" to="net.GND" />
    <trace name="x_c10" from=".C10 > .pin1" to=".Y1 > .pin2" />
    <trace name="x_c10g" from=".C10 > .pin2" to="net.GND" />

    {/* Reset */}
    <trace name="r_nrst" from=".U2 > .NRST" to="net.NRST" />
    <trace name="r_pull" from=".R1 > .pin1" to="net.V3V3" />
    <trace name="r_pull2" from=".R1 > .pin2" to="net.NRST" />
    <trace name="r_sw" from=".SW1 > .pin1" to="net.NRST" />
    <trace name="r_swg" from=".SW1 > .pin2" to="net.GND" />

    {/* SWD */}
    <trace name="d_v" from=".J2 > .pin1" to="net.V3V3" />
    <trace name="d_g" from=".J2 > .pin2" to="net.GND" />
    <trace name="d_io" from=".J2 > .pin3" to=".U2 > .SWDIO" />
    <trace name="d_clk" from=".J2 > .pin4" to=".U2 > .SWCLK" />

    {/* ── I2C gyro ───────────────────────────────────────────────────────── */}
    <trace name="i_scl_mcu" from=".U2 > .PB6_SCL" to="net.SCL" />
    <trace name="i_sda_mcu" from=".U2 > .PB7_SDA" to="net.SDA" />
    <trace name="i_scl_g" from=".U3 > .SCL" to="net.SCL" />
    <trace name="i_sda_g" from=".U3 > .SDA" to="net.SDA" />
    <trace name="i_vdd" from=".U3 > .VDD" to="net.V3V3" />
    <trace name="i_vlogic" from=".U3 > .VLOGIC" to="net.V3V3" />
    <trace name="i_g1" from=".U3 > .GND_1" to="net.GND" />
    <trace name="i_g2" from=".U3 > .GND_2" to="net.GND" />
    <trace name="i_g3" from=".U3 > .GND_3" to="net.GND" />
    <trace name="i_c11" from=".C11 > .pin1" to="net.V3V3" />
    <trace name="i_c11g" from=".C11 > .pin2" to="net.GND" />

    {/* ── GPS on USART1 ──────────────────────────────────────────────────── */}
    <trace name="g_v" from=".J3 > .pin1" to="net.V3V3" />
    <trace name="g_g" from=".J3 > .pin2" to="net.GND" />
    <trace name="g_tx" from=".J3 > .pin3" to=".U2 > .PA10_RX" />
    <trace name="g_rx" from=".J3 > .pin4" to=".U2 > .PA9_TX" />

    {/* ── Motor driver A (motors 1 and 2) ────────────────────────────────── */}
    <trace name="u4_vm" from=".U4 > .VM" to="net.VBAT" thickness="1mm" />
    <trace name="u4_g" from=".U4 > .GND_A" to="net.GND" thickness="0.6mm" />
    <trace name="u4_slp" from=".U4 > .NSLEEP" to="net.V3V3" />
    <trace name="u4_int" from=".U4 > .VINT" to=".C12 > .pin1" />
    <trace name="u4_c12g" from=".C12 > .pin2" to="net.GND" />
    <trace name="u4_vmbulk" from=".C14 > .pin1" to="net.VBAT" thickness="1mm" />
    <trace name="u4_vmbulkg" from=".C14 > .pin2" to="net.GND" thickness="0.6mm" />
    <trace name="u4_a1" from=".U4 > .AIN1" to=".U2 > .PA0_AIN1" />
    <trace name="u4_a2" from=".U4 > .AIN2" to=".U2 > .PA1_AIN2" />
    <trace name="u4_b1" from=".U4 > .BIN1" to=".U2 > .PA2_BIN1" />
    <trace name="u4_b2" from=".U4 > .BIN2" to=".U2 > .PA3_BIN2" />
    <trace name="m1_a" from=".U4 > .AOUT1" to=".J4 > .pin1" thickness="0.6mm" />
    <trace name="m1_b" from=".U4 > .AOUT2" to=".J4 > .pin2" thickness="0.6mm" />
    <trace name="m2_a" from=".U4 > .BOUT1" to=".J5 > .pin1" thickness="0.6mm" />
    <trace name="m2_b" from=".U4 > .BOUT2" to=".J5 > .pin2" thickness="0.6mm" />

    {/* ── Motor driver B (motors 3 and 4) ────────────────────────────────── */}
    <trace name="u5_vm" from=".U5 > .VM" to="net.VBAT" thickness="1mm" />
    <trace name="u5_g" from=".U5 > .GND_A" to="net.GND" thickness="0.6mm" />
    <trace name="u5_slp" from=".U5 > .NSLEEP" to="net.V3V3" />
    <trace name="u5_int" from=".U5 > .VINT" to=".C13 > .pin1" />
    <trace name="u5_c13g" from=".C13 > .pin2" to="net.GND" />
    <trace name="u5_vmbulk" from=".C15 > .pin1" to="net.VBAT" thickness="1mm" />
    <trace name="u5_vmbulkg" from=".C15 > .pin2" to="net.GND" thickness="0.6mm" />
    <trace name="u5_a1" from=".U5 > .AIN1" to=".U2 > .PA4_CIN1" />
    <trace name="u5_a2" from=".U5 > .AIN2" to=".U2 > .PA5_CIN2" />
    <trace name="u5_b1" from=".U5 > .BIN1" to=".U2 > .PB0_DIN1" />
    <trace name="u5_b2" from=".U5 > .BIN2" to=".U2 > .PB1_DIN2" />
    <trace name="m3_a" from=".U5 > .AOUT1" to=".J6 > .pin1" thickness="0.6mm" />
    <trace name="m3_b" from=".U5 > .AOUT2" to=".J6 > .pin2" thickness="0.6mm" />
    <trace name="m4_a" from=".U5 > .BOUT1" to=".J7 > .pin1" thickness="0.6mm" />
    <trace name="m4_b" from=".U5 > .BOUT2" to=".J7 > .pin2" thickness="0.6mm" />

    {/* ── Indicators ─────────────────────────────────────────────────────── */}
    <trace name="l_pwr" from=".R2 > .pin1" to="net.V3V3" />
    <trace name="l_pwr2" from=".R2 > .pin2" to=".D2 > .anode" />
    <trace name="l_pwrg" from=".D2 > .cathode" to="net.GND" />
    <trace name="l_st" from=".R3 > .pin1" to=".U2 > .PB4_LED" />
    <trace name="l_st2" from=".R3 > .pin2" to=".D3 > .anode" />
    <trace name="l_stg" from=".D3 > .cathode" to="net.GND" />
  </board>
)
