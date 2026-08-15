/**
 * Circuit JSON + operating point → a simulatable SPICE deck.
 *
 * Why this is not `circuit-json-to-spice` alone: that converter emits the passives and
 * discretes it recognises and silently drops everything else. Run on the rover it
 * produces 24 lines — every resistor and capacitor, and not one of the five ICs, and no
 * voltage source at all. A deck with no source does not simulate; a deck missing the
 * regulator does not answer the question anyone was asking. Simulating it would have
 * "passed" while checking nothing.
 *
 * So the deck is assembled here, and the parts that have no SPICE model are made
 * explicit rather than dropped:
 *
 *   - **Passives and discretes** get real models. R, C, L directly; LEDs and diodes
 *     from a built-in model card; a MOSFET in a power path becomes its on-resistance.
 *   - **ICs** — MCUs, motor drivers, IMUs — have no usable SPICE model and never will
 *     at this level. They become **behavioural stubs**: a current sink of exactly the
 *     current the operating point says that pin draws. That is honest — it says "this
 *     part takes 40 mA from this rail", which is all a DC rail check needs — and it is
 *     labelled as a stub in the coverage report.
 *   - **Regulators** that source a rail become an ideal voltage source at their output,
 *     because the operating point already declares what that rail should be.
 *
 * Every component lands in exactly one of three buckets — modelled, stubbed, or skipped
 * — and the report names which. Coverage is a number the pipeline can regress on, not a
 * vague feeling that most of the board was simulated.
 */
import type { OperatingPoint } from "../schemas.ts"
import type { BuildResult } from "../types.ts"

export type Coverage = "modelled" | "stubbed" | "skipped"

export interface ComponentCoverage {
  ref: string
  type: string
  coverage: Coverage
  /** Why it landed in that bucket — carried into the report, never dropped. */
  note: string
}

export interface SpiceDeck {
  /** The deck, minus any analysis command — those are appended per claim. */
  text: string
  /** SPICE node name for each Circuit JSON net name. */
  nodeOf: Map<string, string>
  coverage: ComponentCoverage[]
  /** Problems that make the deck untrustworthy rather than merely incomplete. */
  problems: string[]
}

/** Ground aliases. SPICE node 0 is ground by definition. */
const GROUND = new Set(["GND", "GROUND", "VSS", "AGND", "DGND", "0"])

/**
 * SPICE node names cannot carry the punctuation tscircuit's auto-named nets use
 * (`N$12`). Mapping is deterministic and recorded so a report can quote the original.
 */
function toNode(net: string): string {
  if (GROUND.has(net.toUpperCase())) return "0"
  return net.replace(/[^A-Za-z0-9_]/g, "_")
}

/**
 * Component value strings as tscircuit writes them ("100nF", "10kΩ", "220uF") into
 * SPICE's own suffix convention. SPICE reads `MEG` for 10^6 and treats `M` as milli, so
 * a naive pass-through turns 10 kΩ into 10 mΩ.
 */
function toSpiceValue(raw: string | undefined): string | null {
  if (!raw) return null
  const cleaned = raw.replace(/[Ωμ]/g, (c) => (c === "Ω" ? "" : "u")).trim()
  const m = cleaned.match(/^([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)$/)
  if (!m) return null
  const [, num, unit] = m
  const suffix = unit.toLowerCase()
  const scale: Record<string, string> = {
    "": "",
    p: "p", pf: "p",
    n: "n", nf: "n", nh: "n",
    u: "u", uf: "u", uh: "u",
    m: "m", mh: "m",
    k: "k", kω: "k", kohm: "k",
    meg: "meg", mohm: "m",
    f: "", h: "", v: "", a: "", ohm: "",
  }
  const s = scale[suffix]
  if (s === undefined) return null
  return `${num}${s}`
}

/** `U1.VOUT` → `{ ref: "U1", pin: "VOUT" }`. */
function splitPin(ref: string): { ref: string; pin: string } {
  const dot = ref.indexOf(".")
  return dot < 0
    ? { ref, pin: "" }
    : { ref: ref.slice(0, dot), pin: ref.slice(dot + 1) }
}

const MODEL_CARDS = [
  ".model DLED D(IS=1e-20 N=2 RS=10 CJO=2p EG=2.1 BV=5 IBV=10u)",
  ".model DGEN D(IS=1e-14 N=1.5 RS=0.05)",
]

/** Default P-FET / N-FET on-resistance when the HDL does not state one. ASSUMED. */
const DEFAULT_RDS_ON = 0.05

export function buildDeck(args: {
  build: BuildResult
  operatingPoint: OperatingPoint
  title?: string
}): SpiceDeck {
  const { build, operatingPoint: op } = args
  const coverage: ComponentCoverage[] = []
  const problems: string[] = []
  const lines: string[] = []
  const nodeOf = new Map<string, string>()

  for (const net of build.netlist) nodeOf.set(net.name, toNode(net.name))

  /** Every net a given component pin sits on, by `REF.pin`. */
  const netOfPin = new Map<string, string>()
  /** Nets touched by each component, in netlist order. */
  const netsOfRef = new Map<string, string[]>()
  for (const net of build.netlist) {
    for (const conn of net.connections) {
      netOfPin.set(conn, net.name)
      const { ref } = splitPin(conn)
      const list = netsOfRef.get(ref) ?? []
      if (!list.includes(net.name)) list.push(net.name)
      netsOfRef.set(ref, list)
    }
  }

  const nodeForPin = (pinRef: string): string | null => {
    const net = netOfPin.get(pinRef)
    return net ? toNode(net) : null
  }

  /** Two-terminal parts: the nets on pin1 and pin2. */
  const twoTerminal = (ref: string): [string, string] | null => {
    const a = nodeForPin(`${ref}.pin1`)
    const b = nodeForPin(`${ref}.pin2`)
    if (a === null || b === null) return null
    return [a, b]
  }

  // Which refs source a rail — modelled as ideal sources, not as their own silicon.
  const railSourceRefs = new Map<string, { net: string; voltage: number }>()
  for (const rail of op.rails) {
    const { ref } = splitPin(rail.source_pin)
    railSourceRefs.set(ref, { net: rail.net, voltage: rail.voltage_v })
  }

  lines.push(`* ${args.title ?? "pcb-ai deck"} — generated, do not edit`)
  // Every node gets a 1 GΩ path to ground.
  //
  // In DC analysis a capacitor is an open circuit, so any node whose only connections
  // are capacitors and unmodelled IC pins is floating, and the matrix is singular. On
  // the rover that is the crystal node: C9 to ground, Y1 skipped, U2.OSC_IN with no
  // model. ngspice reports "singular matrix: check node n_2" and solves nothing — one
  // untestable corner of the board taking the entire simulation down with it.
  //
  // rshunt is ngspice's own remedy for exactly this. At 1 GΩ a 3.3 V rail leaks 3.3 nA,
  // which is six orders of magnitude below the smallest current anything here claims,
  // so it cannot move a measurement — it only gives the solver a reference.
  lines.push(".options rshunt=1e9")
  lines.push(...MODEL_CARDS)

  // ── Rails as ideal sources ───────────────────────────────────────────────────
  //
  // Each declared rail is driven at its own net. A regulator's output rail is an
  // ideal source because the operating point already asserts what it should be; the
  // question L7 answers is whether the *copper and the loads* hold that voltage up,
  // which is what the resistive network between source and loads decides.
  for (const rail of op.rails) {
    const node = toNode(rail.net)
    if (node === "0") {
      problems.push(`rail "${rail.net}" resolves to ground — it cannot be a supply`)
      continue
    }
    if (!nodeOf.has(rail.net)) {
      problems.push(`rail "${rail.net}" is not a net in the compiled netlist`)
      continue
    }
    lines.push(`V${node} ${node} 0 DC ${rail.voltage_v}`)
  }

  // ── Loads as current sinks ───────────────────────────────────────────────────
  let loadIndex = 0
  for (const load of op.loads) {
    const node = nodeForPin(load.pin) ?? (nodeOf.has(load.net) ? toNode(load.net) : null)
    if (node === null) {
      problems.push(`load pin "${load.pin}" is on no net in the compiled netlist`)
      continue
    }
    if (node === "0") {
      problems.push(`load pin "${load.pin}" sits on ground — it can draw no current`)
      continue
    }
    lines.push(`ILOAD${loadIndex++} ${node} 0 DC ${load.current_a}`)
  }

  // ── Components ───────────────────────────────────────────────────────────────
  for (const c of build.components) {
    const type = c.type.replace(/^simple_/, "")
    const value = toSpiceValue(c.value)
    const nodes = twoTerminal(c.name)

    const skip = (note: string) =>
      coverage.push({ ref: c.name, type, coverage: "skipped", note })
    const stub = (note: string) =>
      coverage.push({ ref: c.name, type, coverage: "stubbed", note })
    const modelled = (note: string) =>
      coverage.push({ ref: c.name, type, coverage: "modelled", note })

    switch (type) {
      case "resistor": {
        if (!nodes || !value) { skip(!nodes ? "pins on no net" : `unparsed value "${c.value}"`); break }
        lines.push(`R${c.name} ${nodes[0]} ${nodes[1]} ${value}`)
        modelled(`R = ${value}`)
        break
      }
      case "capacitor": {
        if (!nodes || !value) { skip(!nodes ? "pins on no net" : `unparsed value "${c.value}"`); break }
        lines.push(`C${c.name} ${nodes[0]} ${nodes[1]} ${value}`)
        modelled(`C = ${value}`)
        break
      }
      case "inductor": {
        if (!nodes || !value) { skip(!nodes ? "pins on no net" : `unparsed value "${c.value}"`); break }
        lines.push(`L${c.name} ${nodes[0]} ${nodes[1]} ${value}`)
        modelled(`L = ${value}`)
        break
      }
      case "led": {
        if (!nodes) { skip("pins on no net"); break }
        lines.push(`D${c.name} ${nodes[0]} ${nodes[1]} DLED`)
        modelled("built-in LED model card")
        break
      }
      case "diode": {
        if (!nodes) { skip("pins on no net"); break }
        lines.push(`D${c.name} ${nodes[0]} ${nodes[1]} DGEN`)
        modelled("built-in generic diode model card")
        break
      }
      case "mosfet": {
        // A FET in a power path is there to conduct. Its channel resistance is what the
        // rail sees; its switching behaviour is not a DC question. Modelling it as
        // R_ds(on) keeps the conduction path real and states the assumption out loud.
        const conducting = netsOfRef.get(c.name)?.filter((n) => toNode(n) !== "0") ?? []
        if (conducting.length < 2) { skip("fewer than two non-ground nets"); break }
        lines.push(
          `R${c.name}_DSON ${toNode(conducting[0])} ${toNode(conducting[1])} ${DEFAULT_RDS_ON}`,
        )
        stub(`R_ds(on) = ${DEFAULT_RDS_ON} Ω, ASSUMED — not from a datasheet`)
        break
      }
      case "crystal":
      case "resonator": {
        skip("passive resonator: no DC path, and timing is not a DC-analysis question")
        break
      }
      case "pin_header":
      case "connector": {
        skip("connector: no device")
        break
      }
      case "push_button":
      case "switch": {
        skip("switch: modelled open; its closed state is a separate scenario")
        break
      }
      case "chip": {
        const rail = railSourceRefs.get(c.name)
        if (rail) {
          // Already represented by the ideal source on its output rail above.
          stub(`sources rail ${rail.net} — represented as an ideal ${rail.voltage} V source`)
          break
        }
        const pins = op.loads.filter((l) => splitPin(l.pin).ref === c.name)
        if (pins.length) {
          const total = pins.reduce((s, l) => s + l.current_a, 0)
          stub(
            `behavioural: ${pins.length} current sink(s) totalling ${total.toFixed(3)} A ` +
              `from the operating point`,
          )
        } else {
          skip("no SPICE model and no declared load current — contributes nothing to the deck")
        }
        break
      }
      default: {
        if (nodes && value) {
          lines.push(`R${c.name} ${nodes[0]} ${nodes[1]} ${value}`)
          modelled(`unrecognised type "${type}" with a parseable value — treated as a resistance`)
        } else {
          skip(`no model for type "${type}"`)
        }
      }
    }
  }

  // A deck with no source cannot be simulated, and a deck with no load tells you
  // nothing about drop. Both are pipeline failures, not simulation results.
  if (!op.rails.length) problems.push("operating point declares no rails — nothing to drive the deck")
  if (!op.loads.length) problems.push("operating point declares no loads — every rail will read exactly its source voltage")

  return { text: lines.join("\n"), nodeOf, coverage, problems }
}

export function coverageSummary(coverage: ComponentCoverage[]): {
  modelled: number
  stubbed: number
  skipped: number
  percent: number
} {
  const modelled = coverage.filter((c) => c.coverage === "modelled").length
  const stubbed = coverage.filter((c) => c.coverage === "stubbed").length
  const skipped = coverage.filter((c) => c.coverage === "skipped").length
  const total = coverage.length || 1
  return {
    modelled,
    stubbed,
    skipped,
    percent: ((modelled + stubbed) / total) * 100,
  }
}
