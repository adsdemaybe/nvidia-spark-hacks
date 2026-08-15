/**
 * The HDL reference handed to the model. tscircuit is a React-flavoured hardware
 * description language: JSX elements are components, props are their electrical and
 * physical parameters, and the runtime compiles the tree into Circuit JSON
 * (netlist + schematic geometry + PCB geometry) which we then render and review.
 *
 * Keep this file factual. Every element name below is taken from the installed
 * @tscircuit/core type definitions.
 */

/** Footprint strings the toolchain understands, shared with the parts agent. */
export const FOOTPRINTS = [
  "0201, 0402, 0603, 0805, 1206, 1210, 2010, 2512 (chip passives)",
  "sod123, sot23, sot23-5, sot223, to220, to92 (discretes)",
  "soic8, soic14, soic16, tssop8, tssop16, tssop20, dip8, dip14, dip16 (ICs)",
  "qfn16, qfn24, qfn32, lqfp32, lqfp48, lqfp64 (fine-pitch ICs)",
  "pinrow2 through pinrow40 (headers)",
  "pad, pushbutton",
].join("; ")

export const HDL_GUIDE = `
# tscircuit HDL reference

A design is a single .tsx module with a default-exported component. The runtime
compiles it to Circuit JSON, from which schematic, PCB and assembly views are rendered.

## Skeleton

\`\`\`tsx
export default () => (
  <board width="30mm" height="20mm">
    {/* components and traces */}
  </board>
)
\`\`\`

Rules:
- Exactly one default export, and it must be a component (arrow function returning JSX).
- No imports. No npm packages, no @tsci/* registry imports, no local files.
- Everything lives inside a single <board>. Give the board explicit width/height in mm.
- Plain JSX only — no hooks, no async, no side effects.

## Available elements

Passives and discretes:
  resistor capacitor inductor diode led fuse crystal resonator battery
  potentiometer transistor mosfet switch pushbutton solderjumper jumper testpoint

Integrated / connectors:
  chip opamp connector pinheader powersource voltagesource currentsource

Structure and connectivity:
  board group subcircuit net trace netlabel bus differentialpair

PCB features:
  via platedhole hole smtpad cutout copperpour keepout fiducial
  silkscreentext silkscreenpath silkscreenline silkscreenrect silkscreencircle
  tracehint courtyardrect footprint

Schematic-only annotation:
  schematicbox schematictext schematictable schematicline

## Component props

Every component needs a unique \`name\`. Anything that appears on the PCB needs a
\`footprint\`.

  <resistor name="R1" resistance="10k" footprint="0402" pcbX={-5} pcbY={0} schX={-2} schY={0} />
  <capacitor name="C1" capacitance="100nF" footprint="0402" pcbX={0} pcbY={3} />
  <led name="D1" footprint="0603" color="red" pcbX={5} />
  <diode name="D2" footprint="sod123" />
  <inductor name="L1" inductance="10uH" footprint="0805" />
  <crystal name="Y1" frequency="16MHz" loadCapacitance="20pF" footprint="csm-3" />
  <pushbutton name="SW1" footprint="pushbutton" />
  <pinheader name="J1" pinCount={4} footprint="pinrow4" pitch="2.54mm" />
  <testpoint name="TP1" footprint="pad" />

Position props (all in mm, all optional but strongly recommended):
  pcbX pcbY pcbRotation  — placement on the board
  schX schY schRotation  — placement on the schematic sheet
  layer="top" | "bottom"

Common footprint strings: 0201 0402 0603 0805 1206 1210 2010 2512,
sod123 sot23 sot223 to220 to92, soic8 soic14 soic16, tssop8 tssop16 tssop20,
qfn16 qfn24 qfn32, lqfp32 lqfp48 lqfp64, dip8 dip14 dip16,
pinrow2 ... pinrow40, pad, pushbutton.
A footprint can also be written inline as JSX:
  <chip name="U1" footprint={<footprint><smtpad pcbX={-1} pcbY={0} width="1mm" height="0.6mm" shape="rect" portHints={["1"]} /></footprint>} />

## Chips

A chip needs its pins named, otherwise nothing can be wired to it.

  <chip
    name="U1"
    footprint="soic8"
    manufacturerPartNumber="NE555"
    pinLabels={{
      pin1: "GND",
      pin2: "TRIG",
      pin3: "OUT",
      pin4: "RESET",
      pin5: "CTRL",
      pin6: "THRES",
      pin7: "DISCH",
      pin8: "VCC",
    }}
    schPinArrangement={{
      leftSide:  { direction: "top-to-bottom", pins: ["TRIG", "THRES", "CTRL", "RESET"] },
      rightSide: { direction: "top-to-bottom", pins: ["OUT", "DISCH"] },
      topSide:   { direction: "left-to-right", pins: ["VCC"] },
      bottomSide:{ direction: "left-to-right", pins: ["GND"] },
    }}
    pinAttributes={{
      VCC: { requiresPower: true },
      GND: { requiresGround: true },
    }}
    pcbX={0}
    pcbY={0}
  />

\`schPinArrangement\` is optional but produces a far more readable schematic:
put power on top, ground on bottom, inputs left, outputs right.

\`pinAttributes\` marks which pins need power/ground so the compiler can check the
rails are actually connected. Declare it on every chip:
\`requiresPower\`, \`requiresGround\`, \`providesPower\`, \`providesGround\`.

## Connectivity

Named nets — declare once, reference anywhere:

  <net name="VCC" />
  <net name="GND" />

Traces, one connection each. Always give a trace a \`name\` — unnamed traces are
harder to talk about in review and raise a warning:

  <trace name="R1_D1"  from=".R1 > .pin2" to=".D1 > .anode" />
  <trace name="U1_VCC" from=".U1 > .VCC"  to="net.VCC" />
  <trace name="U1_GND" from=".U1 > .GND"  to="net.GND" />

Selector syntax:
  .R1 > .pin1      pin by number
  .D1 > .anode     pin by semantic name (anode/cathode, pos/neg, pin1/pin2)
  .U1 > .OUT       chip pin by its pinLabels name
  net.GND          a named net

A component may instead declare its own connections inline, which is terser for
chips with many pins:

  <chip name="U1" footprint="soic8" connections={{ VCC: "net.VCC", GND: "net.GND", OUT: ".R1 > .pin1" }} />

Add \`<netlabel>\` to make power rails readable on the schematic:

  <netlabel net="VCC" anchorSide="bottom" schX={0} schY={4} />
  <netlabel net="GND" anchorSide="top"    schX={0} schY={-4} />

## Routing

PCB traces are autorouted from the schematic connectivity by default — you do not
place copper by hand. Influence it with placement (\`pcbX\`/\`pcbY\`) rather than by
hand-drawing traces. If a specific trace needs to be wider:

  <trace from=".U1 > .VBUS" to="net.VBUS" thickness="0.4mm" />

## Rules that will otherwise cost you an iteration

- Every pin of every component should end up on a net. Unconnected pins raise
  \`source_pin_missing_trace_warning\`.
- Two-terminal parts use \`.pin1\`/\`.pin2\`; LEDs and diodes also accept
  \`.anode\`/\`.cathode\`; capacitors also accept \`.pos\`/\`.neg\`.
- Components with no \`pcbX\`/\`pcbY\` stack at the origin and overlap. Always place.
- Keep every part inside the board outline: |pcbX| < width/2, |pcbY| < height/2,
  with at least 1mm of margin for the part body.
- Decouple every IC power pin with a 100nF cap placed within ~2mm of the pin.
- Do not invent props. An unrecognised prop raises \`source_property_ignored_warning\`
  and is silently dropped.
- Values are strings with units: "10k", "4.7uF", "16MHz", "0.4mm".

## What gets analysed afterwards

Your design is compiled, then solved: a steady-state thermal field, a DC IR-drop field
per supply rail, IPC-2221 trace-current capacity, geometric DRC against the board's own
fab limits, and electrical rule checks. Design so those come out clean:

- Give every supply rail a \`<net>\` with a real name (VCC, VBUS, V3V3, GND). The
  analysis is per-named-rail; an unnamed rail is not solved.

## Decoupling capacitor placement — the one rule that will cost you the whole board

Any capacitor wired power-to-ground is automatically treated as a decoupling capacitor
and given a **1mm maximum trace length**. If its pad ends up further than 1mm from the
pin it decouples, the compiler does not warn — it **skips autorouting for the entire
board** and emits a cascade of "Ports [A, B] are not connected" and "Trace ... has no
PCB trace" errors for every net on the board. Those errors are consequences. Adding the
missing traces will not fix them, because the traces are already there.

So place the cap by arithmetic, not by eye. It must be close enough to satisfy the 1mm
rule and far enough not to collide with the IC body:

    minimum centre-to-centre = IC_width/2 + cap_width/2 + 0.3mm clearance
    the cap pad must still land within 1mm of the power pin

For a soic8 (about 5.3 x 4.4mm) with an 0402 cap (1.0 x 0.5mm), that means sitting the
cap just off the package edge, level with the power pin — roughly 3.2mm from the IC
centre along the pin's side, not above or below it. Never give the cap the same
\`pcbX\`/\`pcbY\` as the IC: they will overlap and the placement DRC will fail.

If you genuinely cannot get within 1mm, set the constraint explicitly rather than
letting the board fail to route:

  <capacitor name="C1" capacitance="100nF" footprint="0402" maxDecouplingTraceLength="3mm" />
- Widen power traces carrying real current: \`<trace thickness="0.4mm">\` or more. The
  default 0.15mm is fine for signals and marginal above ~0.5A.
- Spread heat. A part dissipating hundreds of milliwatts on an island of copper runs
  much hotter than the same part with copper to conduct into; give it pad area or a
  pour, and keep it away from anything temperature-sensitive.
- Keep the current path short and direct from the supply pin to the loads. IR drop is
  solved on the copper you actually routed, so a rail that reaches a load by a long
  thin detour shows up as a drop.
`.trim()
