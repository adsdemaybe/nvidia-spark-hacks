/**
 * Handoff formats — the board in forms other tools can open.
 *
 * Gerbers are what a fab needs and nothing else can read. Everything downstream of this
 * pipeline wants something different: a human wants to open the board in KiCad and poke
 * at it, `text-to-cad` wants solid geometry to design an enclosure around, and Isaac /
 * MuJoCo want a mesh with mass. This module writes the two that need no root and no new
 * solver, because the converters already exist and the placement data is already in
 * Circuit JSON (plan §10.1).
 *
 *   - **KiCad project** (`.kicad_pro` + `.kicad_sch` + `.kicad_pcb`) — opens in the
 *     KiCad GUI. This matters more than it sounds: `kicad-cli` is not installable on
 *     this Spark without root (apt offers only 7.x), so the second-opinion DRC of plan
 *     §3.2 cannot run here. Emitting the project anyway means a human, or any machine
 *     that does have KiCad, can run that check — the gate is unavailable, not the data.
 *   - **GLB** — the 3D board with its parts placed, for the viewer, for CAD, and as the
 *     visual half of the simulation asset in plan §10.5.
 *
 * Every export is best-effort and reports rather than throws: a board that fails to
 * convert to GLB is still a board with valid Gerbers, and losing the run over a viewer
 * format would be the tail wagging the dog.
 */
import fs from "node:fs/promises"
import path from "node:path"

export interface ExportResult {
  name: string
  file?: string
  bytes?: number
  error?: string
}

/**
 * Write a KiCad project: schematic, board, and the project file that ties them together.
 *
 * All three are written even when one converter is unhappy, because KiCad opens a
 * project with a missing board perfectly well and shows you the schematic.
 */
export async function exportKicad(
  circuitJson: any[],
  dir: string,
  name = "board",
): Promise<ExportResult[]> {
  const results: ExportResult[] = []
  await fs.mkdir(dir, { recursive: true })

  const kicad = await import("circuit-json-to-kicad")

  // These converters are staged pipelines, not one-shot functions: constructing one and
  // calling getOutputString() straight away returns a valid-but-empty file — a
  // `(kicad_pcb (generator …))` header with no board in it, which opens in KiCad and
  // shows nothing. `runUntilFinished()` is what actually walks the Circuit JSON.
  const drive = (converter: any) => {
    converter.runUntilFinished()
    return converter.getOutputString()
  }

  const jobs: Array<{ label: string; ext: string; run: () => string }> = [
    {
      label: "kicad_pcb",
      ext: ".kicad_pcb",
      run: () => drive(new kicad.CircuitJsonToKicadPcbConverter(circuitJson as any)),
    },
    {
      label: "kicad_sch",
      ext: ".kicad_sch",
      run: () => drive(new kicad.CircuitJsonToKicadSchConverter(circuitJson as any)),
    },
    {
      label: "kicad_pro",
      ext: ".kicad_pro",
      run: () => drive(new kicad.CircuitJsonToKicadProConverter(circuitJson as any)),
    },
  ]

  for (const job of jobs) {
    try {
      const text = job.run()
      const file = path.join(dir, `${name}${job.ext}`)
      await fs.writeFile(file, text, "utf8")
      results.push({ name: job.label, file, bytes: Buffer.byteLength(text) })
    } catch (err) {
      results.push({ name: job.label, error: (err as Error).message.split("\n")[0] })
    }
  }
  return results
}

/** Write the board and its placed parts as a single GLB. */
export async function exportGlb(
  circuitJson: any[],
  dir: string,
  name = "board",
): Promise<ExportResult> {
  await fs.mkdir(dir, { recursive: true })
  const file = path.join(dir, `${name}.glb`)
  try {
    const { convertCircuitJsonToGltf } = await import("circuit-json-to-gltf")
    const glb = await convertCircuitJsonToGltf(circuitJson as any, { format: "glb" })
    const buffer = Buffer.isBuffer(glb)
      ? glb
      : glb instanceof ArrayBuffer
        ? Buffer.from(glb)
        : Buffer.from(JSON.stringify(glb))
    await fs.writeFile(file, buffer)
    return { name: "glb", file, bytes: buffer.byteLength }
  } catch (err) {
    return { name: "glb", error: (err as Error).message.split("\n")[0] }
  }
}

/** Everything in one call, for the pipeline's artifact stage. */
export async function exportHandoff(
  circuitJson: any[],
  dir: string,
  name = "board",
): Promise<ExportResult[]> {
  const kicad = await exportKicad(circuitJson, path.join(dir, "kicad"), name)
  const glb = await exportGlb(circuitJson, dir, name)
  return [...kicad, glb]
}

export function describeExports(results: ExportResult[]): string {
  return results
    .map((r) =>
      r.error
        ? `  ${r.name.padEnd(10)} FAILED — ${r.error}`
        : `  ${r.name.padEnd(10)} ${r.file}  (${((r.bytes ?? 0) / 1024).toFixed(1)} kB)`,
    )
    .join("\n")
}
