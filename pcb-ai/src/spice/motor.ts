/**
 * Brushed DC motors — the boundary where electrical becomes mechanical.
 *
 * Two equations, and everything in the co-simulation hangs off them:
 *
 *     electrical:   V = I·R + L·dI/dt + Ke·ω        (back-EMF opposes the supply)
 *     mechanical:   τ = Kt·I                        (current makes torque)
 *
 * In SI units **Kt and Ke are numerically identical** for a brushed DC motor — one is
 * N·m/A, the other V·s/rad, and they are the same constant seen from the two sides of
 * the same energy conversion. That is not a simplification; it is what makes the
 * coupling in `electromechanical-cosim-plan.md` §4.1 honest. A model that lets them
 * drift apart is inventing or destroying energy.
 *
 * **Every constant here is `ASSUMED`.** No datasheet has been consulted, because no
 * motor has been chosen. They are representative of their class and internally
 * consistent — stall current, no-load speed and rated torque all follow from R, Ke and
 * the supply rather than being typed in separately — so a rollout against them shows the
 * *shape* of the answer. It is not a number to quote until a real part is picked, and
 * every report says so.
 *
 * Override by naming a motor in the claim, or by supplying the constants directly. A
 * user who knows their part should never have to edit this file.
 */

export type Provenance = "CONFIRMED" | "INFERRED" | "ASSUMED" | "MEASURED"

export interface Motor {
  id: string
  description: string
  /** Winding resistance, ohms. Sets stall current: I_stall = V / R. */
  R_ohm: number
  /** Winding inductance, henries. Sets how fast current can follow PWM. */
  L_h: number
  /**
   * Torque constant, N·m/A — and, numerically, the back-EMF constant in V·s/rad.
   * One field because they are one constant.
   */
  Kt_nm_per_a: number
  /** Rotor inertia, kg·m². Only matters to the mechanical side. */
  J_kgm2: number
  /** Viscous damping, N·m·s/rad. */
  b_nms_per_rad: number
  /** Gearbox reduction, output:motor. 1 for a direct drive. */
  gear_ratio: number
  /** What the motor is designed to run at, for sanity-checking a rollout. */
  rated_v: number
  provenance: Provenance
  note: string
}

/**
 * A small catalogue spanning the range that matters for a rover on a 2S pack:
 * one motor comfortably inside a DRV8833's rating, one near it, and one that
 * deliberately exceeds it.
 *
 * The third is not filler. A catalogue where everything passes cannot demonstrate that
 * `stall_current` works, and a gate nobody has seen fail is a gate nobody should trust.
 */
export const MOTORS: Record<string, Motor> = {
  "n20-6v": {
    id: "n20-6v",
    description: "N20 micro gearmotor, 6 V class — typical of a small indoor rover",
    R_ohm: 4.0,
    L_h: 0.0015,
    Kt_nm_per_a: 0.0055,
    J_kgm2: 1.2e-7,
    b_nms_per_rad: 2.0e-7,
    gear_ratio: 100,
    rated_v: 6,
    provenance: "ASSUMED",
    note: "representative of the class; intrinsic stall ~1.85 A at 7.4 V, ~1.51 A " +
      "delivered through a DRV8833 — just inside the channel rating",
  },
  "tt-gearmotor": {
    id: "tt-gearmotor",
    description: "TT / yellow-gearbox motor, 3–6 V — the common hobby rover drive",
    R_ohm: 5.5,
    L_h: 0.0020,
    Kt_nm_per_a: 0.0095,
    J_kgm2: 3.0e-7,
    b_nms_per_rad: 5.0e-7,
    gear_ratio: 48,
    rated_v: 6,
    provenance: "ASSUMED",
    note: "higher torque constant, lower speed; intrinsic stall ~1.35 A at 7.4 V, " +
      "~1.16 A delivered",
  },
  "775-12v": {
    id: "775-12v",
    description: "775-class brushed motor, 12 V — deliberately too big for a DRV8833",
    R_ohm: 0.7,
    L_h: 0.0005,
    Kt_nm_per_a: 0.021,
    J_kgm2: 2.5e-5,
    b_nms_per_rad: 1.0e-5,
    gear_ratio: 1,
    rated_v: 12,
    provenance: "ASSUMED",
    note: "intrinsic stall ~10.6 A at 7.4 V; ~4.7 A actually delivered through a " +
      "DRV8833 and its copper — still 3x the channel rating. Included so the " +
      "stall_current gate can be seen failing on a real case",
  },
}

export const DEFAULT_MOTOR = "n20-6v"

export function getMotor(idOrMotor: string | Motor | undefined): Motor {
  if (!idOrMotor) return MOTORS[DEFAULT_MOTOR]
  if (typeof idOrMotor !== "string") return idOrMotor
  const m = MOTORS[idOrMotor]
  if (!m) {
    throw new Error(
      `unknown motor "${idOrMotor}". Known: ${Object.keys(MOTORS).join(", ")}. ` +
        `Supply the constants directly to use a part that is not in the catalogue.`,
    )
  }
  return m
}

/**
 * Everything that follows from R, Kt and a supply voltage.
 *
 * Derived rather than tabulated so the numbers cannot contradict each other — a
 * catalogue that lists a stall current inconsistent with its own resistance is worse
 * than one that lists none.
 */
export interface MotorOperatingEnvelope {
  /**
   * The motor's *intrinsic* stall current, V/R_winding — what it would draw across an
   * ideal supply.
   *
   * The current a real drive delivers is always lower, because the bridge's
   * on-resistance, the copper and the supply's own impedance are in series with the
   * winding. The 775 below stalls at 10.6 A intrinsically and only 4.65 A through a
   * DRV8833 on a 2S pack. Both numbers are true and they answer different questions:
   * this one sizes the motor, the transient sizes the driver.
   */
  stall_current_a: number
  stall_torque_nm: number
  no_load_speed_rad_s: number
  no_load_speed_rpm: number
  /** Output-shaft values, after the gearbox. */
  output_stall_torque_nm: number
  output_no_load_rpm: number
  /** Electrical time constant L/R — how fast current follows a PWM edge. */
  tau_electrical_s: number
}

export function envelope(motor: Motor, supply_v: number): MotorOperatingEnvelope {
  const stall_current_a = supply_v / motor.R_ohm
  const stall_torque_nm = motor.Kt_nm_per_a * stall_current_a
  // At no load the back-EMF very nearly cancels the supply; the residual drives only
  // friction, which this ignores — the standard first-order figure.
  const no_load_speed_rad_s = supply_v / motor.Kt_nm_per_a
  return {
    stall_current_a,
    stall_torque_nm,
    no_load_speed_rad_s,
    no_load_speed_rpm: (no_load_speed_rad_s * 60) / (2 * Math.PI),
    output_stall_torque_nm: stall_torque_nm * motor.gear_ratio,
    output_no_load_rpm: (no_load_speed_rad_s * 60) / (2 * Math.PI) / motor.gear_ratio,
    tau_electrical_s: motor.L_h / motor.R_ohm,
  }
}

/** Back-EMF at a given shaft speed, volts. The term that couples the two simulators. */
export function backEmf(motor: Motor, omega_rad_s: number): number {
  return motor.Kt_nm_per_a * omega_rad_s
}

/** Torque from current, N·m at the motor shaft (before the gearbox). */
export function torqueOf(motor: Motor, current_a: number): number {
  return motor.Kt_nm_per_a * current_a
}

export function describeMotor(motor: Motor, supply_v: number): string {
  const e = envelope(motor, supply_v)
  return [
    `${motor.id} — ${motor.description}`,
    `  R = ${motor.R_ohm} Ω, L = ${(motor.L_h * 1000).toFixed(2)} mH, ` +
      `Kt = Ke = ${motor.Kt_nm_per_a} N·m/A, gear ${motor.gear_ratio}:1   [${motor.provenance}]`,
    `  at ${supply_v} V: stall ${e.stall_current_a.toFixed(2)} A / ` +
      `${e.stall_torque_nm.toFixed(4)} N·m, no-load ${e.no_load_speed_rpm.toFixed(0)} rpm ` +
      `(${e.output_no_load_rpm.toFixed(0)} rpm at the output)`,
    `  electrical time constant L/R = ${(e.tau_electrical_s * 1e6).toFixed(0)} µs`,
    `  ${motor.note}`,
  ].join("\n")
}
