/**
 * Transient analysis — the behaviour engine of the electromechanical loop.
 *
 * L7's steady-state claims answer "does the rail hold its voltage". This answers the
 * question that decides whether a robot moves: **over one control period, with the PWM
 * the firmware is commanding and the shaft turning at the speed the mechanics report,
 * how much current actually reaches the motor and what torque does it make?**
 *
 * DC analysis structurally cannot answer it. Duty cycle, winding inductance, gate-drive
 * delay and back-EMF are all time-domain, and the case that destroys hardware — a
 * stalled motor drawing its full V/R — looks identical to normal operation until you
 * look at the waveform.
 *
 * The deck is small on purpose. It models the path that carries the current:
 *
 *     supply ──[ R_supply ]── bridge ──[ R_ds(on) + R_trace ]── R_m ── L_m ── Ke·ω ── gnd
 *
 * Everything else on the board is irrelevant to this question and would only slow the
 * solve. This is the lumped-element argument from the co-sim plan §2 applied concretely:
 * the loop needs thousands of these, so each one must cost milliseconds.
 */
import path from "node:path"
import { simulate } from "./run.ts"
import { backEmf, envelope, getMotor, torqueOf, type Motor } from "./motor.ts"

export interface DriveCommand {
  /** PWM duty, 0..1. */
  duty: number
  /** PWM carrier, Hz. */
  freq_hz: number
  /** Shaft speed this period, rad/s, from the mechanical side. 0 = stalled. */
  omega_rad_s: number
}

export interface DriveTopology {
  /** Supply rail feeding the bridge, volts. */
  supply_v: number
  /** Source impedance of the rail: battery ESR + bulk cap ESR + upstream copper. */
  r_supply_ohm: number
  /** Bridge on-resistance, both legs (high + low side). */
  r_bridge_ohm: number
  /** Copper from bridge to motor and back. */
  r_trace_ohm: number
  /** Bridge propagation delay, seconds — why a real driver is not an ideal switch. */
  gate_delay_s: number
}

/** A DRV8833 on the rover's 2S pack, which is what the demo board actually uses. */
export const DEFAULT_TOPOLOGY: DriveTopology = {
  supply_v: 7.4,
  r_supply_ohm: 0.12,
  r_bridge_ohm: 0.72,
  r_trace_ohm: 0.05,
  gate_delay_s: 300e-9,
}

export interface TransientResult {
  ok: boolean
  /** Mean motor current over the measured window, amps. */
  current_avg_a: number
  /** Largest instantaneous current, amps — what the driver and copper must survive. */
  current_peak_a: number
  /** Mean torque at the motor shaft, N·m. */
  torque_avg_nm: number
  /** Mean torque at the output shaft, after the gearbox. */
  output_torque_avg_nm: number
  /** Mean voltage actually across the motor, after every drop. */
  motor_voltage_avg_v: number
  /** How far the rail sagged at its worst, millivolts. */
  supply_sag_peak_mv: number
  deckPath: string
  warnings: string[]
  error?: string
}

/**
 * Build the deck for one control period.
 *
 * `omega` is held constant across the period — the Jacobi coupling described in the
 * co-sim plan §4.2. It is an approximation, and a legitimate one only while the
 * mechanical time constant is much longer than the period; the plan says what to do when
 * it is not.
 */
export function buildTransientDeck(args: {
  motor: Motor
  drive: DriveCommand
  topology: DriveTopology
  /** Simulated span. Defaults to 8 PWM cycles, enough for current to reach steady state. */
  span_s?: number
}): { deck: string; span_s: number; settle_s: number } {
  const { motor, drive, topology: t } = args
  const period_s = 1 / drive.freq_hz
  const tau_e = motor.L_h / motor.R_ohm

  // The span must cover the SLOWER of the two time constants, not the faster.
  //
  // Sizing it at "8 PWM cycles" seemed obviously right and was wrong: at 20 kHz that is
  // 400 µs, while this motor's L/R is 375 µs, so the winding current was still on its
  // first ramp when the measurement window closed. The reported average was ~0 A for a
  // stall that actually draws 1.5 A. Six time constants is >99% settled.
  const span_s = args.span_s ?? Math.max(period_s * 10, tau_e * 6)
  // Measure over the last quarter, by which point both the PWM ripple and the ramp have
  // settled into steady operation.
  const settle_s = span_s * 0.75

  const emf = backEmf(motor, drive.omega_rad_s)
  const onTime = Math.max(0, Math.min(1, drive.duty)) * period_s
  // A PULSE source with finite edges: an ideal step would ask the solver for infinite
  // di/dt through an inductor and is also a lie about a real gate driver.
  const edge = Math.max(t.gate_delay_s, 1e-9)

  const lines = [
    `* transient drive — ${motor.id} @ ${(drive.duty * 100).toFixed(0)}% duty, ` +
      `${drive.freq_hz} Hz, ω = ${drive.omega_rad_s.toFixed(1)} rad/s`,
    ".options rshunt=1e9",
    "",
    `* supply and its source impedance`,
    `VSUP supply 0 DC ${t.supply_v}`,
    `RSUP supply bridge_in ${t.r_supply_ohm}`,
    "",
    `* the bridge as a switched source: on-resistance while on, open while off`,
    // A PULSE whose width equals its period is degenerate — ngspice has no "always on"
    // pulse, and asking for one produces a source that does not switch cleanly. Full and
    // zero duty are DC by definition, so say so.
    drive.duty >= 0.999
      ? `VPWM gate 0 DC 1`
      : onTime <= 0
        ? `VPWM gate 0 DC 0`
        : `VPWM gate 0 PULSE(0 1 0 ${edge.toExponential(3)} ${edge.toExponential(3)} ` +
          `${onTime.toExponential(6)} ${period_s.toExponential(6)})`,
    `SBRIDGE bridge_in bridge_out gate 0 SWMOD`,
    `.model SWMOD SW(Ron=${t.r_bridge_ohm} Roff=1e9 Vt=0.5 Vh=0.1)`,
    "",
    `* copper from the bridge to the motor`,
    `RTRACE bridge_out mot_p ${t.r_trace_ohm}`,
    "",
    `* freewheel path — the low-side body diode`,
    //
    // Without this the model is not merely inaccurate, it is unphysical. When the bridge
    // opens, the winding's inductance insists on maintaining its current; with nowhere to
    // go, ngspice drives the node hugely negative and the average current collapses. The
    // symptom was a 10%-duty stall reporting 0.001 A while 100% duty (which never
    // switches) gave a correct 1.51 A — the giveaway that the fault was in switching, not
    // in the motor.
    //
    // A real H-bridge always has this path: the body diode of the opposite FET, or that
    // FET turned on for synchronous decay. Anode at ground, cathode at the motor terminal,
    // so it conducts exactly when the node is pulled below ground.
    `DFW 0 mot_p DFWMOD`,
    `.model DFWMOD D(IS=1e-12 N=1 RS=0.02)`,
    "",
    `* the motor: winding, inductance, and back-EMF proportional to shaft speed`,
    `RM mot_p mot_l ${motor.R_ohm}`,
    `LM mot_l mot_emf ${motor.L_h.toExponential(6)}`,
    `VEMF mot_emf 0 DC ${emf.toExponential(6)}`,
    "",
    ".control",
    `tran ${(period_s / 200).toExponential(3)} ${span_s.toExponential(6)} uic`,
    // .meas is ngspice's own windowed statistics — more trustworthy than averaging a
    // vector by hand, and it prints as `name = value` which the parser already reads.
    `meas tran i_avg AVG i(VEMF) from=${settle_s.toExponential(6)} to=${span_s.toExponential(6)}`,
    `meas tran i_pk MAX i(VEMF) from=${settle_s.toExponential(6)} to=${span_s.toExponential(6)}`,
    `meas tran v_mot AVG v(mot_p) from=${settle_s.toExponential(6)} to=${span_s.toExponential(6)}`,
    `meas tran v_rail MIN v(bridge_in) from=${settle_s.toExponential(6)} to=${span_s.toExponential(6)}`,
    ".endc",
    ".end",
    "",
  ]
  return { deck: lines.join("\n"), span_s, settle_s }
}

export async function runTransient(args: {
  motor: Motor | string
  drive: DriveCommand
  topology?: DriveTopology
  dir: string
  label?: string
}): Promise<TransientResult> {
  const motor = getMotor(args.motor)
  const topology = args.topology ?? DEFAULT_TOPOLOGY
  const { deck } = buildTransientDeck({ motor, drive: args.drive, topology })
  const name = args.label ?? "transient"

  const sim = await simulate({ deck, dir: args.dir, name })
  const deckPath = path.join(args.dir, `${name}.cir`)

  const empty: TransientResult = {
    ok: false,
    current_avg_a: 0,
    current_peak_a: 0,
    torque_avg_nm: 0,
    output_torque_avg_nm: 0,
    motor_voltage_avg_v: 0,
    supply_sag_peak_mv: 0,
    deckPath,
    warnings: [],
  }
  if (!sim.ok) return { ...empty, error: sim.error ?? "the transient did not solve" }

  // Current through VEMF is measured in the source's own reference direction, which is
  // opposite to motor current. Sign is a convention, not physics — take the magnitude
  // and say so rather than leaving a mysterious minus in the report.
  const iAvg = Math.abs(sim.values.get("i_avg") ?? 0)
  const iPk = Math.abs(sim.values.get("i_pk") ?? 0)
  const vMot = sim.values.get("v_mot") ?? 0
  const vRail = sim.values.get("v_rail") ?? topology.supply_v

  const warnings: string[] = []
  if (!sim.values.has("i_avg")) {
    warnings.push("ngspice returned no i_avg — the measurement window may be empty")
  }
  const env = envelope(motor, topology.supply_v)
  if (iPk > env.stall_current_a * 1.05) {
    warnings.push(
      `peak current ${iPk.toFixed(2)} A exceeds this motor's stall current ` +
        `${env.stall_current_a.toFixed(2)} A — check the deck, not the design`,
    )
  }

  return {
    ok: true,
    current_avg_a: iAvg,
    current_peak_a: iPk,
    torque_avg_nm: torqueOf(motor, iAvg),
    output_torque_avg_nm: torqueOf(motor, iAvg) * motor.gear_ratio,
    motor_voltage_avg_v: vMot,
    supply_sag_peak_mv: Math.max(0, (topology.supply_v - vRail) * 1000),
    deckPath,
    warnings,
  }
}

export function describeTransient(r: TransientResult, motor: Motor): string {
  if (!r.ok) return `TRANSIENT  FAILED — ${r.error}`
  return [
    `TRANSIENT  ${motor.id}`,
    `  current    ${r.current_avg_a.toFixed(3)} A average, ${r.current_peak_a.toFixed(3)} A peak`,
    `  torque     ${(r.torque_avg_nm * 1000).toFixed(2)} mN·m at the shaft, ` +
      `${(r.output_torque_avg_nm * 1000).toFixed(1)} mN·m at the output`,
    `  motor      ${r.motor_voltage_avg_v.toFixed(2)} V average across the winding`,
    `  rail sag   ${r.supply_sag_peak_mv.toFixed(0)} mV worst`,
    ...r.warnings.map((w) => `  ! ${w}`),
  ].join("\n")
}
