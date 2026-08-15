#!/usr/bin/env tsx
/**
 * M1 of the electromechanical loop, standalone: drive a PWM into a motor and see what
 * current and torque come out.
 *
 *   npx tsx tools/motor-sim.ts                          # default motor, a duty sweep
 *   npx tsx tools/motor-sim.ts --motor tt-gearmotor --duty 0.6 --omega 40
 *   npx tsx tools/motor-sim.ts --sweep stall            # every motor, stalled
 *
 * The sweep is the useful mode. One operating point is a number; a sweep across duty or
 * speed shows whether the model behaves like a motor — current rising with duty, falling
 * with speed as back-EMF opposes the supply — which is the thing worth checking before
 * anything is wired to MuJoCo.
 */
import { parseArgs } from "node:util"
import { MOTORS, describeMotor, envelope, getMotor } from "../src/spice/motor.ts"
import { DEFAULT_TOPOLOGY, runTransient } from "../src/spice/transient.ts"
import { locateNgspice } from "../src/spice/run.ts"

const { values } = parseArgs({
  options: {
    motor: { type: "string" },
    duty: { type: "string" },
    omega: { type: "string" },
    freq: { type: "string", default: "20000" },
    sweep: { type: "string" },
    json: { type: "boolean", default: false },
    out: { type: "string", default: "runs/motor-sim" },
    list: { type: "boolean", default: false },
  },
})

if (values.list) {
  for (const m of Object.values(MOTORS)) {
    console.log(describeMotor(m, DEFAULT_TOPOLOGY.supply_v))
    console.log()
  }
  process.exit(0)
}

// Machine-readable single point, for the co-simulation's electrical participant.
// Emitted before any of the human-facing banner so a caller can take the last JSON line
// without parsing prose.
if (values.json) {
  const m = getMotor(values.motor)
  const r = await runTransient({
    motor: m,
    drive: {
      duty: Number(values.duty ?? 1),
      freq_hz: Number(values.freq),
      omega_rad_s: Number(values.omega ?? 0),
    },
    dir: values.out!,
    label: "point",
  })
  console.log(JSON.stringify({ motor: m.id, ok: r.ok, error: r.error ?? null, ...r }))
  process.exit(r.ok ? 0 : 1)
}

const ng = await locateNgspice()
if (!ng) {
  console.error("ngspice not found — run ./tools/vendor-ngspice.sh first")
  process.exit(1)
}
console.log(`ngspice-${ng.version} (${ng.source})`)

const motor = getMotor(values.motor)
const freq_hz = Number(values.freq)
console.log()
console.log(describeMotor(motor, DEFAULT_TOPOLOGY.supply_v))
console.log()
console.log(
  `supply ${DEFAULT_TOPOLOGY.supply_v} V through ` +
    `${(DEFAULT_TOPOLOGY.r_supply_ohm + DEFAULT_TOPOLOGY.r_bridge_ohm + DEFAULT_TOPOLOGY.r_trace_ohm).toFixed(2)} Ω ` +
    `of source + bridge + copper, PWM at ${(freq_hz / 1000).toFixed(0)} kHz`,
)
console.log()

const fmt = (n: number, w: number, d = 3) => n.toFixed(d).padStart(w)

/** One row of the table, so every mode prints the same columns. */
async function point(m: typeof motor, duty: number, omega: number, label: string) {
  const r = await runTransient({
    motor: m,
    drive: { duty, freq_hz, omega_rad_s: omega },
    dir: values.out!,
    // The label is a filename as well as a column, and "ω = 0 rad/s" is not one.
    label: label.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_|_$/g, ""),
  })
  if (!r.ok) {
    console.log(`  ${label.padEnd(22)} FAILED — ${r.error}`)
    return null
  }
  console.log(
    `  ${label.padEnd(22)}${fmt(r.current_avg_a, 8)} A ${fmt(r.current_peak_a, 8)} A ` +
      `${fmt(r.torque_avg_nm * 1000, 9, 2)} mN·m ${fmt(r.output_torque_avg_nm * 1000, 10, 1)} mN·m ` +
      `${fmt(r.motor_voltage_avg_v, 8, 2)} V ${fmt(r.supply_sag_peak_mv, 8, 0)} mV`,
  )
  for (const w of r.warnings) console.log(`      ! ${w}`)
  return r
}

const header =
  "                          I avg    I peak     torque      output    V motor  rail sag"

if (values.sweep === "stall") {
  // Every motor held at zero speed and full duty: the case that destroys drivers.
  console.log("STALL — full duty, shaft held at 0 rad/s (no back-EMF to limit current)")
  console.log(header)
  for (const m of Object.values(MOTORS)) {
    await point(m, 1.0, 0, m.id)
  }
  console.log()
  console.log("A DRV8833 channel is rated ~1.5 A RMS. Anything above that is the gate's job.")
} else if (values.sweep === "speed") {
  console.log("SPEED SWEEP — full duty, rising shaft speed (back-EMF should cut current)")
  console.log(header)
  const noLoad = envelope(motor, DEFAULT_TOPOLOGY.supply_v).no_load_speed_rad_s
  for (const frac of [0, 0.25, 0.5, 0.75, 0.95]) {
    await point(motor, 1.0, noLoad * frac, `ω = ${(noLoad * frac).toFixed(0)} rad/s`)
  }
} else if (values.duty !== undefined || values.omega !== undefined) {
  console.log(header)
  await point(motor, Number(values.duty ?? 1), Number(values.omega ?? 0), "operating point")
} else {
  console.log("DUTY SWEEP — shaft stalled, rising PWM duty (current should track duty)")
  console.log(header)
  for (const duty of [0.1, 0.25, 0.5, 0.75, 1.0]) {
    await point(motor, duty, 0, `duty ${(duty * 100).toFixed(0)}%`)
  }
}

console.log()
console.log(`decks and logs → ${values.out}`)
