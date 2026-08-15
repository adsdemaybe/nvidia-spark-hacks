/**
 * Offline stub model — a test fixture, not a fallback.
 *
 * It exists so the graph itself can be exercised without a provider: every node runs,
 * state flows, the fan-out and the loop-back edge fire, and every artifact is written.
 * What it cannot test is whether a real model produces good designs — only that the
 * machinery around it is wired correctly.
 *
 * Responses are keyed off the `name` passed to `withStructuredOutput`, so adding a new
 * structured call means adding a case here or the stub run fails loudly.
 */
import { AIMessage, type BaseMessage } from "@langchain/core/messages"

const STUB_HDL = `
export default () => (
  <board width="20mm" height="16mm">
    <net name="VCC" />
    <net name="GND" />
    <resistor name="R1" resistance="330" footprint="0402" pcbX={-3} pcbY={0} schX={-2} />
    <led name="D1" color="red" footprint="0603" pcbX={3} pcbY={0} schX={2} />
    <pinheader name="J1" pinCount={2} footprint="pinrow2" pitch="2.54mm" pcbX={-7} pcbY={-5} schX={-6} />
    <trace name="VCC_R1" from=".J1 > .pin1" to="net.VCC" />
    <trace name="GND_J1" from=".J1 > .pin2" to="net.GND" />
    <trace name="R1_IN" from=".R1 > .pin1" to="net.VCC" />
    <trace name="R1_D1" from=".R1 > .pin2" to=".D1 > .anode" />
    <trace name="D1_GND" from=".D1 > .cathode" to="net.GND" />
  </board>
)
`.trim()

export class StubChatModel {
  /** Text turns: the designer. Returns a fixed, compilable board. */
  async invoke(_messages: BaseMessage[]): Promise<AIMessage> {
    return new AIMessage(`\`\`\`tsx\n${STUB_HDL}\n\`\`\``)
  }

  withStructuredOutput<T>(_schema: unknown, config?: Record<string, unknown>) {
    const name = String(config?.name ?? "")
    return {
      invoke: async (_messages: BaseMessage[]): Promise<T> => {
        switch (name) {
          case "probe":
            // The --check preflight. Answering it keeps `--check --model stub` a
            // valid way to exercise the check path itself.
            return { ok: true, colour: "red" } as T

          case "parts_plan":
            return {
              topology: "stub: single resistor and LED",
              rationale: "stub model: no topology decision made.",
              alternatives_rejected: [],
              rails: [{ name: "VCC", voltage_v: 5, role: "supply" }],
              parts: [
                {
                  ref: "R1",
                  kind: "resistor",
                  value: "330",
                  footprint: "0402",
                  role: "current limit",
                  key_specs: "1/16W",
                  rationale: "stub",
                },
                {
                  ref: "D1",
                  kind: "led",
                  value: "red",
                  footprint: "0603",
                  role: "indicator",
                  key_specs: "Vf 2.0V",
                  rationale: "stub",
                },
              ],
              key_calculations: ["stub: no calculations performed"],
              layout_constraints: [],
              placement_rules: [
                {
                  kind: "at_edge",
                  refs: ["J1"],
                  edge: "west",
                  layer: null,
                  max_mm: null,
                  axis: null,
                  why: "stub: exercises the placement checker without asserting anything real.",
                },
              ],
              risks: [],
            } as T

          case "operating_point":
            return {
              ambient_c: 25,
              rails: [
                { net: "VCC", voltage_v: 5, source_pin: "J1.pin1", max_drop_mv: 50 },
              ],
              loads: [{ pin: "R1.pin1", net: "VCC", current_a: 0.009 }],
              dissipation: [
                { component: "R1", power_w: 0.027, max_temp_c: 125 },
                { component: "D1", power_w: 0.018, max_temp_c: 85 },
              ],
              assumptions: ["stub model: fixed operating point, not derived from the netlist"],
            } as T

          case "review":
            return {
              summary: "stub model: no review performed.",
              findings: [],
            } as T

          case "verdict":
            // Passing on the first verdict keeps a stub run to one iteration; the
            // loop-back edge is exercised separately by the failing-stub fixture.
            return {
              pass: true,
              summary: "stub model: accepted without review.",
              work_order: [],
            } as T

          default:
            throw new Error(
              `StubChatModel has no canned response for structured call "${name}". ` +
                `Add one in src/models/stub.ts.`,
            )
        }
      },
    }
  }
}

/**
 * Variant that never passes, so a stub run exercises the revise → compile loop-back
 * edge and the iteration budget.
 */
export class StubChatModelAlwaysRevise extends StubChatModel {
  override withStructuredOutput<T>(schema: unknown, config?: Record<string, unknown>) {
    const inner = super.withStructuredOutput<T>(schema, config)
    const name = String(config?.name ?? "")
    if (name !== "verdict") return inner
    return {
      invoke: async (messages: BaseMessage[]): Promise<T> =>
        ({
          pass: false,
          summary: "stub model: forcing a revision to exercise the loop.",
          work_order: [
            {
              severity: "major",
              category: "placement",
              description: "stub model: synthetic finding.",
              suggested_fix: "No change required; this exists to drive another iteration.",
              source: "stub",
            },
          ],
        }) as T,
    }
  }
}
