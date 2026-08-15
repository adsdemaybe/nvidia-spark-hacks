/** Final step: turn the accepted Circuit JSON into fabrication outputs. */
import fs from "node:fs/promises"
import path from "node:path"
import {
  convertSoupToGerberCommands,
  stringifyGerberCommandLayers,
  convertSoupToExcellonDrillCommands,
  stringifyExcellonDrill,
} from "circuit-json-to-gerber"
import { convertCircuitJsonToBomRows, convertBomRowsToCsv } from "circuit-json-to-bom-csv"

/** Writes Gerbers, drill files and a BOM into `dir`. Returns what it produced. */
export async function exportFabrication(circuitJson: any[], dir: string): Promise<string[]> {
  await fs.mkdir(dir, { recursive: true })
  const written: string[] = []

  try {
    const layers = stringifyGerberCommandLayers(convertSoupToGerberCommands(circuitJson as any))
    for (const [layer, text] of Object.entries(layers)) {
      const file = path.join(dir, `${layer}.gbr`)
      await fs.writeFile(file, text as string)
      written.push(file)
    }

    for (const plated of [true, false]) {
      const drill = stringifyExcellonDrill(
        convertSoupToExcellonDrillCommands({
          circuitJson: circuitJson as any,
          is_plated: plated,
        }),
      )
      const file = path.join(dir, plated ? "plated.drl" : "unplated.drl")
      await fs.writeFile(file, drill)
      written.push(file)
    }
  } catch (err) {
    console.warn(`  ! gerber export failed: ${(err as Error).message}`)
  }

  try {
    const csv = await convertBomRowsToCsv(await convertCircuitJsonToBomRows({ circuitJson } as any))
    const file = path.join(dir, "bom.csv")
    await fs.writeFile(file, csv)
    written.push(file)
  } catch (err) {
    console.warn(`  ! BOM export failed: ${(err as Error).message}`)
  }

  return written
}
