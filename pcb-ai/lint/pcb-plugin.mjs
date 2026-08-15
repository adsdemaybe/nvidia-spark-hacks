/**
 * eslint-plugin-pcb — electronics-aware lint rules for tscircuit HDL.
 *
 * Engine is typescript-eslint; these rules encode the failures that each cost a full
 * compile-route-review iteration to discover during the PoC. A rule earns its place
 * here by having burned real wall-clock time.
 */

/** Elements accepted by the installed @tscircuit/core (extracted from its typedefs). */
const KNOWN_ELEMENTS = new Set([
  "resistor", "capacitor", "inductor", "pushbutton", "diode", "fuse", "led", "board",
  "jumper", "solderjumper", "potentiometer", "chip", "pinout", "powersource", "via",
  "netlabel", "net", "trace", "bus", "differentialpair", "crystal", "footprint",
  "smtpad", "platedhole", "keepout", "hole", "port", "group", "opamp", "cadmodel",
  "battery", "connector", "pinheader", "resonator", "subcircuit", "transistor",
  "switch", "mosfet", "testpoint", "voltagesource", "currentsource", "copperpour",
  "silkscreentext", "silkscreenpath", "silkscreenline", "silkscreenrect",
  "silkscreencircle", "tracehint", "courtyardrect", "courtyardoutline", "cutout",
  "fanout", "fanoutpoint", "breakout", "breakoutpoint", "constraint", "jscad",
  "schematicbox", "schematictext", "schematicline", "schematicrect", "schematictable",
  "analogsimulation", "voltageprobe", "spicemodel", "fiducial", "testpoint",
])

/** value-with-units props that must not be bare numbers on the wire. */
const DISTANCE_PROPS = new Set(["maxDecouplingTraceLength", "maxTraceLength", "thickness", "pitch"])

const getJsxName = (node) =>
  node.name?.type === "JSXIdentifier" ? node.name.name : null

const getAttr = (node, name) =>
  node.attributes?.find((a) => a.type === "JSXAttribute" && a.name?.name === name)

const literalOf = (attr) => {
  if (!attr?.value) return undefined
  if (attr.value.type === "Literal") return attr.value.value
  if (attr.value.type === "JSXExpressionContainer" && attr.value.expression.type === "Literal")
    return attr.value.expression.value
  return undefined
}

/** rule: only elements the compiler actually knows. Unknown ones silently no-op. */
const knownElements = {
  meta: {
    type: "problem",
    docs: { description: "JSX element must be a tscircuit element known to @tscircuit/core" },
    messages: {
      unknown:
        'Unknown tscircuit element <{{name}}>. The compiler drops it silently. Known elements include: board, chip, resistor, capacitor, trace, net, pinheader, crystal, mosfet…',
    },
  },
  create: (ctx) => ({
    JSXOpeningElement(node) {
      const name = getJsxName(node)
      // Uppercase = user React component; those are fine.
      if (!name || /^[A-Z]/.test(name)) return
      if (!KNOWN_ELEMENTS.has(name)) {
        ctx.report({ node, messageId: "unknown", data: { name } })
      }
    },
  }),
}

/**
 * rule: selector integrity. Every ".X > .pin" in a trace must name a component that
 * exists and, when pinLabels are declared, a pin that exists on it. The `.U2 > .SWCLK`
 * typo class — one bad selector cascaded to 60 errors in the PoC.
 */
const traceSelectors = {
  meta: {
    type: "problem",
    docs: { description: "trace from/to selectors must reference declared components and pins" },
    messages: {
      noComponent: 'Trace references "{{sel}}" but no component named "{{comp}}" exists in this file.',
      noPin: 'Trace references pin "{{pin}}" on "{{comp}}", but its pinLabels only declare: {{known}}.',
      noNet: 'Trace references "net.{{net}}" but no <net name="{{net}}" /> is declared.',
    },
  },
  create: (ctx) => {
    const components = new Map() // name -> Set(pinNames) | null when unknowable
    const nets = new Set()
    const pending = []

    const recordComponent = (node) => {
      const name = literalOf(getAttr(node, "name"))
      if (typeof name !== "string") return
      const el = getJsxName(node)
      if (el === "net") {
        nets.add(name)
        return
      }
      let pins = null // null = any pin allowed (no pinLabels declared)
      const labels = getAttr(node, "pinLabels")
      if (labels?.value?.type === "JSXExpressionContainer" &&
          labels.value.expression.type === "ObjectExpression") {
        pins = new Set()
        for (const prop of labels.value.expression.properties) {
          if (prop.type !== "Property") continue
          const key = prop.key.name ?? prop.key.value
          if (typeof key === "string") pins.add(key)
          if (prop.value.type === "Literal" && typeof prop.value.value === "string")
            pins.add(prop.value.value)
        }
      }
      components.set(name, pins)
    }

    const checkSelector = (node, sel) => {
      if (typeof sel !== "string") return
      const netMatch = sel.match(/^net\.(\S+)$/)
      if (netMatch) {
        pending.push(() => {
          if (!nets.has(netMatch[1]))
            ctx.report({ node, messageId: "noNet", data: { net: netMatch[1] } })
        })
        return
      }
      const m = sel.match(/^\.(\S+)\s*>\s*\.(\S+)$/)
      if (!m) return
      const [, comp, pin] = m
      pending.push(() => {
        if (!components.has(comp)) {
          ctx.report({ node, messageId: "noComponent", data: { sel, comp } })
          return
        }
        const pins = components.get(comp)
        if (pins && !pins.has(pin) && !/^(pin\d+|anode|cathode|pos|neg|left|right|drain|source|gate|base|collector|emitter)$/.test(pin)) {
          ctx.report({
            node, messageId: "noPin",
            data: { pin, comp, known: [...pins].join(", ") },
          })
        }
      })
    }

    return {
      JSXOpeningElement(node) {
        const el = getJsxName(node)
        if (!el) return
        if (el === "trace") {
          checkSelector(node, literalOf(getAttr(node, "from")))
          checkSelector(node, literalOf(getAttr(node, "to")))
        } else {
          recordComponent(node)
        }
      },
      "Program:exit"() {
        for (const check of pending) check()
      },
    }
  },
}

/**
 * rule: the 1mm decoupling trap. A power-to-ground capacitor gets a silent 1mm max
 * trace length; violating it aborts autorouting for the entire board and cascades
 * phantom "missing trace" errors. Force the constraint to be stated.
 */
const POWER_NET = /^(vcc|vdd|vbat|vbus|vin|v\d|v3v3|v5|pwr|power)/i
const GROUND_NET = /^(gnd|ground|vss|agnd|dgnd)/i

const decouplingLength = {
  meta: {
    type: "problem",
    docs: { description: "power-to-ground capacitors need an explicit maxDecouplingTraceLength" },
    messages: {
      implicit:
        '<capacitor name="{{name}}"> is wired power-to-ground ({{power}} / {{ground}}) with no maxDecouplingTraceLength. tscircuit imposes a silent 1mm limit on BOTH legs and aborts autorouting for the whole board when unmet. State the real constraint.',
    },
  },
  create: (ctx) => {
    // Which nets touch each capacitor — decided at Program:exit so declaration
    // order does not matter. Only power-to-ground topology triggers tscircuit's
    // hidden rule, so only that topology is flagged (a crystal load cap is fine).
    const caps = new Map() // name -> node (caps missing the prop)
    const capNets = new Map() // name -> Set(net names)

    return {
      JSXOpeningElement(node) {
        const el = getJsxName(node)
        if (el === "capacitor") {
          const name = literalOf(getAttr(node, "name"))
          if (typeof name === "string" && !getAttr(node, "maxDecouplingTraceLength")) {
            caps.set(name, node)
          }
          return
        }
        if (el !== "trace") return
        for (const side of ["from", "to"]) {
          const sel = literalOf(getAttr(node, side))
          if (typeof sel !== "string") continue
          const comp = sel.match(/^\.(\S+)\s*>/)?.[1]
          const other = side === "from" ? "to" : "from"
          const otherSel = literalOf(getAttr(node, other))
          const net = typeof otherSel === "string" ? otherSel.match(/^net\.(\S+)$/)?.[1] : undefined
          if (comp && net) {
            if (!capNets.has(comp)) capNets.set(comp, new Set())
            capNets.get(comp).add(net)
          }
        }
      },
      "Program:exit"() {
        for (const [name, node] of caps) {
          const nets = [...(capNets.get(name) ?? [])]
          const power = nets.find((n) => POWER_NET.test(n))
          const ground = nets.find((n) => GROUND_NET.test(n))
          if (power && ground) {
            ctx.report({ node, messageId: "implicit", data: { name, power, ground } })
          }
        }
      },
    }
  },
}

/** rule: the 10mm crystal trap — same failure mode, different constant. */
const crystalLength = {
  meta: {
    type: "problem",
    docs: { description: "crystals need an explicit maxTraceLength" },
    messages: {
      implicit:
        "<crystal {{name}}> has no maxTraceLength. tscircuit imposes a silent 10mm limit; under auto-placement (pcbPack) the crystal can land further away and abort routing for the whole board.",
    },
  },
  create: (ctx) => ({
    JSXOpeningElement(node) {
      if (getJsxName(node) !== "crystal") return
      if (!getAttr(node, "maxTraceLength")) {
        const name = literalOf(getAttr(node, "name")) ?? "?"
        ctx.report({ node, messageId: "implicit", data: { name: `name="${name}"` } })
      }
    },
  }),
}

/**
 * rule: pcbRotation rotates pads but not the courtyard, so the placement DRC then
 * checks the wrong rectangle. Cost one blinker iteration.
 */
const noPcbRotation = {
  meta: {
    type: "problem",
    docs: { description: "pcbRotation misaligns the courtyard used by placement DRC" },
    messages: {
      rotated:
        "pcbRotation rotates the pads but NOT the courtyard rectangle — placement DRC then validates the wrong box. Place without rotation, or verify the courtyard against the compiled output.",
    },
  },
  create: (ctx) => ({
    JSXAttribute(node) {
      if (node.name?.name === "pcbRotation") {
        ctx.report({ node, messageId: "rotated" })
      }
    },
  }),
}

/** rule: units. `thickness={0.4}` is not `thickness="0.4mm"`. */
const unitStrings = {
  meta: {
    type: "problem",
    docs: { description: "distance props take unit strings, not bare numbers" },
    messages: {
      bare: '{{prop}} is a distance — write it as a unit string (e.g. "{{val}}mm"), not a bare number.',
    },
  },
  create: (ctx) => ({
    JSXAttribute(node) {
      const prop = node.name?.name
      if (!DISTANCE_PROPS.has(prop)) return
      const v = literalOf(node)
      if (typeof v === "number") {
        ctx.report({ node, messageId: "bare", data: { prop, val: String(v) } })
      }
    },
  }),
}

/** rule: chips must declare power/ground intent so the compiler can check the rails. */
/**
 * `pinLabels` on a pinheader/jumper must be an object keyed by pin number, not an array.
 *
 * This rule exists because the loop could not converge without it, and the reason is
 * uncomfortable: **the tscircuit docs are wrong here.** `pinheader.mdx` shows
 * `pinLabels={["VCC", "GND", ...]}`, the compiler rejects exactly that with
 * `Invalid props for pinheader "J1": pinLabels ({"0":{"_errors":["Invalid"]}...)`, and the
 * object form compiles clean. Measured all three ways on a two-pin header: array -> 3
 * errors and 0 parts, object -> 0 errors, omitted -> 0 errors.
 *
 * That made it unfixable by the design loop rather than merely wrong. The designer reads
 * the retrieved documentation and writes the array form; the compiler rejects it; the
 * reviewer reads the same documentation and its work order says, in as many words,
 * `pinLabels=["3.3V","GND"]`; the model applies that faithfully; the error is
 * byte-identical next iteration. Four rounds of an agreeing, confident, wrong loop.
 *
 * Catching it at lint puts a correct instruction in front of both agents before compile
 * ever runs, which is the only place in the chain that outranks the upstream docs.
 */
const PIN_LABEL_ELEMENTS = new Set(["pinheader", "jumper", "chip", "connector"])

const pinheaderPinLabels = {
  meta: {
    type: "problem",
    docs: { description: "pinheader/jumper pinLabels must be keyed by pin number" },
    messages: {
      array:
        '<{{el}} {{name}}> passes pinLabels as an array. That is the form the tscircuit docs show and the compiler rejects it — pin keys are 1-based, so index 0 is invalid, and the whole component fails to create. Use an object keyed by pin number: pinLabels={{ 1: "3.3V", 2: "GND" }}. Omitting pinLabels also compiles.',
    },
  },
  create: (ctx) => ({
    JSXOpeningElement(node) {
      const el = getJsxName(node)
      // Every element that takes pinLabels, not just pinheader. `chip` was checked
      // separately and fails identically -- array form 3 errors and 0 parts, object form
      // clean -- and chip.mdx documents the array form too. So this is not one page being
      // out of date, it is the documented shape being wrong across the elements that use
      // the prop, which is a much better reason for the rule to exist.
      if (!PIN_LABEL_ELEMENTS.has(el)) return
      const attr = getAttr(node, "pinLabels")
      if (!attr) return
      const expr = attr.value && attr.value.type === "JSXExpressionContainer"
        ? attr.value.expression
        : null
      if (expr && expr.type === "ArrayExpression") {
        const name = literalOf(getAttr(node, "name")) ?? "?"
        ctx.report({ node, messageId: "array", data: { el, name: `name="${name}"` } })
      }
    },
  }),
}

const chipPinAttributes = {
  meta: {
    type: "suggestion",
    docs: { description: "chips should declare pinAttributes for power/ground checking" },
    messages: {
      missing:
        '<chip {{name}}> declares no pinAttributes. Without requiresPower/requiresGround the compiler cannot verify the rails actually reach this part.',
    },
  },
  create: (ctx) => ({
    JSXOpeningElement(node) {
      if (getJsxName(node) !== "chip") return
      if (!getAttr(node, "pinAttributes")) {
        const name = literalOf(getAttr(node, "name")) ?? "?"
        ctx.report({ node, messageId: "missing", data: { name: `name="${name}"` } })
      }
    },
  }),
}

/** rule: duplicate reference designators compile into colliding selectors. */
const uniqueNames = {
  meta: {
    type: "problem",
    docs: { description: "component names must be unique within a board" },
    messages: { dupe: 'Duplicate component name "{{name}}" — selectors will be ambiguous.' },
  },
  create: (ctx) => {
    const seen = new Map()
    return {
      JSXOpeningElement(node) {
        const el = getJsxName(node)
        if (!el || el === "net" || el === "trace" || el === "netlabel" || /^[A-Z]/.test(el)) return
        const name = literalOf(getAttr(node, "name"))
        if (typeof name !== "string") return
        if (seen.has(name)) {
          ctx.report({ node, messageId: "dupe", data: { name } })
        }
        seen.set(name, node)
      },
    }
  },
}

export default {
  meta: { name: "eslint-plugin-pcb", version: "0.1.0" },
  rules: {
    "known-elements": knownElements,
    "trace-selectors": traceSelectors,
    "decoupling-length": decouplingLength,
    "crystal-length": crystalLength,
    "no-pcb-rotation": noPcbRotation,
    "unit-strings": unitStrings,
    "chip-pin-attributes": chipPinAttributes,
    "pinheader-pin-labels": pinheaderPinLabels,
    "unique-names": uniqueNames,
  },
}
