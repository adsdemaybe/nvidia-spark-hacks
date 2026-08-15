/**
 * The operating-point model: what the board actually does electrically.
 *
 * Circuit JSON says what is connected, not how much current flows or how much power
 * each part burns — and no amount of geometry gets you there. That is a modelling
 * judgement, so it is produced by a model (see `src/agents/modeler.ts`) and consumed
 * here as data. The solvers below do arithmetic on it and nothing else.
 */

export interface RailModel {
  net: string
  voltage_v: number
  /** Pin that sources the rail, e.g. "J1.pin1". Anchors the IR-drop solve. */
  source_pin: string
  /** Acceptable drop before the rail is considered out of spec. */
  max_drop_mv: number
}

export interface LoadModel {
  /** Pin drawing current, e.g. "U1.VCC". */
  pin: string
  net: string
  current_a: number
}

export interface DissipationModel {
  component: string
  power_w: number
  /** Datasheet maximum operating temperature. */
  max_temp_c: number
}

export interface OperatingPoint {
  ambient_c: number
  rails: RailModel[]
  loads: LoadModel[]
  dissipation: DissipationModel[]
  /** Free-text record of the assumptions behind the numbers, carried into the report. */
  assumptions: string[]
}

/** JSON Schema handed to the modelling agent. */
export const OPERATING_POINT_SCHEMA = {
  type: "object",
  properties: {
    ambient_c: { type: "number", description: "Ambient temperature in Celsius, typically 25." },
    rails: {
      type: "array",
      items: {
        type: "object",
        properties: {
          net: { type: "string", description: "Net name as it appears in the netlist." },
          voltage_v: { type: "number" },
          source_pin: {
            type: "string",
            description: "Pin that feeds the rail, in Component.pin form, e.g. J1.pin1.",
          },
          max_drop_mv: {
            type: "number",
            description: "Largest acceptable IR drop on this rail, in millivolts.",
          },
        },
        required: ["net", "voltage_v", "source_pin", "max_drop_mv"],
        additionalProperties: false,
      },
    },
    loads: {
      type: "array",
      items: {
        type: "object",
        properties: {
          pin: { type: "string", description: "Current-drawing pin, e.g. U1.VCC." },
          net: { type: "string" },
          current_a: { type: "number", description: "Steady-state current in amperes." },
        },
        required: ["pin", "net", "current_a"],
        additionalProperties: false,
      },
    },
    dissipation: {
      type: "array",
      items: {
        type: "object",
        properties: {
          component: { type: "string" },
          power_w: { type: "number", description: "Steady-state dissipation in watts." },
          max_temp_c: {
            type: "number",
            description: "Maximum operating temperature from the datasheet, in Celsius.",
          },
        },
        required: ["component", "power_w", "max_temp_c"],
        additionalProperties: false,
      },
    },
    assumptions: {
      type: "array",
      items: { type: "string" },
      description:
        "Every assumption behind the numbers: supply voltage, duty cycle, LED forward voltage, quiescent currents, worst case vs typical.",
    },
  },
  required: ["ambient_c", "rails", "loads", "dissipation", "assumptions"],
  additionalProperties: false,
} as const
