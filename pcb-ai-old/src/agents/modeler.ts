/**
 * The modelling agent: works out what the board does electrically so the solvers
 * have something to solve.
 *
 * Circuit JSON gives connectivity and geometry. It does not give supply voltages,
 * currents or dissipation, and those cannot be derived from geometry — they follow
 * from what the parts are and what the design is for. That is a judgement, so a model
 * makes it, states its assumptions, and everything downstream is arithmetic.
 */
import { askStructured, type ChatLike } from "../model.ts"
import { describeBuild } from "../build.ts"
import { OperatingPointSchema, type OperatingPoint } from "../schemas.ts"
import type { BuildResult } from "../types.ts"

const SYSTEM = `
You are an electrical engineer establishing the DC operating point of a board that has
already been designed, so it can be analysed thermally and for power integrity.

You are given the specification, the HDL, and the compiled netlist. Work out:

- Every supply rail: its voltage, the pin that feeds it, and how much drop is
  acceptable on it. Use a tight budget for anything feeding an IC (typically 1-3% of
  the rail), a looser one for LEDs and other non-critical loads.
- Every load: which pin draws current from which rail, and how much, in amperes.
  Include IC quiescent current, LED forward current computed from the actual series
  resistor and forward voltage, and pull-up/divider currents where they matter.
- Every part that dissipates meaningful power, in watts, with the maximum operating
  temperature from its datasheet class. Include series resistors (I²R), LEDs
  (I·Vf), linear regulators (Vin−Vout)·Iout, and IC quiescent dissipation.
  Small-signal parts under a milliwatt can be omitted.

Rules:
- Use net names and Component.pin names exactly as they appear in the netlist. A name
  that does not match is silently dropped and the analysis is wrong.
- Prefer worst-case steady state over typical. A blinking LED at 50% duty is still
  modelled at its DC current unless the specification says otherwise.
- Compute values from the actual components in the netlist. Do not use placeholders.
- Record every assumption. Downstream reports quote them, and a wrong assumption is
  much easier to catch when it is written down than when it is buried in a number.
`.trim()

export function modelOperatingPoint(args: {
  model: ChatLike
  spec: string
  code: string
  build: BuildResult
}): Promise<OperatingPoint> {
  const { model, spec, code, build } = args
  return askStructured<OperatingPoint>(
    model,
    OperatingPointSchema,
    "operating_point",
    SYSTEM,
    [
      `<specification>\n${spec}\n</specification>`,
      `<hdl>\n${code}\n</hdl>`,
      `<netlist>\n${describeBuild(build)}\n</netlist>`,
      "Establish the operating point.",
    ].join("\n\n"),
  )
}
