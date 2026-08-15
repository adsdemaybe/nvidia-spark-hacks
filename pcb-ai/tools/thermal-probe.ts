/**
 * Every component's temperature, including the ones the report suppresses.
 *
 * `physics.txt` hides a zero-dissipation part once its margin passes 30 °C, which is the
 * right default for a report a human reads — but it makes an improvement invisible. C1 on
 * rover-power went from 75 °C to comfortably cool when it moved out of Q1's plume, and the
 * only visible sign was the line disappearing. This prints the whole table, so a placement
 * change can be measured rather than inferred from an absence.
 */
import fs from "node:fs/promises"
import { parseArgs } from "node:util"
import { analyzeThermal } from "../src/physics/thermal.ts"

const { values } = parseArgs({ options: { run: { type: "string" } } })
if (!values.run) {
  console.error("usage: thermal-probe --run <dir containing circuit.json + operating-point.json>")
  process.exit(1)
}
const circuit = JSON.parse(await fs.readFile(`${values.run}/circuit.json`, "utf8"))
const op = JSON.parse(await fs.readFile(`${values.run}/operating-point.json`, "utf8"))
const t = analyzeThermal(circuit, op)
if (!t) {
  console.error("no thermal result")
  process.exit(1)
}
console.log(`peak ${t.peak_c.toFixed(1)}°C, total ${t.total_power_w.toFixed(3)} W`)
for (const c of [...t.components].sort((a, b) => b.temperature_c - a.temperature_c)) {
  const mean = c.mean_temperature_c
  console.log(
    `  ${c.component.padEnd(4)} ${c.temperature_c.toFixed(1).padStart(6)}°C hotspot` +
      (mean !== undefined ? ` (${mean.toFixed(1)}°C mean)` : "") +
      ` at ${c.power_w.toFixed(3)}W  margin ${c.margin_c.toFixed(1)}°C`,
  )
}
