/**
 * TwinStateProvider (STRUCT_2.md 25).
 *
 * The renderer must not care which of these is behind it. That is what lets the
 * Isaac bridge replace the fixture stream with no client rewrite -- and it is
 * why the mock lives behind the same interface rather than being special-cased
 * inside the renderer.
 *
 * Whichever is connected, the UI has to say so. Presenting fixture state as
 * live Isaac state is explicitly forbidden (STRUCT_2.md 82).
 */

import type { TwinState } from "./contracts";
import { parseTwinState } from "./contracts";

export type TwinListener = (state: TwinState) => void;
export type ProvenanceListener = (provenance: TwinProvenance) => void;

export interface TwinProvenance {
  /** What the user must be told the robot's state actually came from. */
  label: string;
  live: boolean;
  connected: boolean;
}

export interface TwinStateProvider {
  readonly provenance: TwinProvenance;
  start(onState: TwinListener, onProvenance?: ProvenanceListener): void;
  stop(): void;
}

/** Replays the committed fixture stream, looping. No network, no Spark. */
export class MockTwinStateProvider implements TwinStateProvider {
  readonly provenance: TwinProvenance = {
    label: "SIMULATION DISCONNECTED — USING FIXTURE STREAM",
    live: false,
    connected: true,
  };

  private timer: ReturnType<typeof setInterval> | undefined;
  private index = 0;

  constructor(
    private readonly states: TwinState[],
    private readonly hz = 30,
  ) {}

  static async load(url: string, hz = 30): Promise<MockTwinStateProvider> {
    const text = await fetch(url).then((r) => r.text());
    const states = text
      .split("\n")
      .filter((line) => line.trim())
      .map(parseTwinState);
    return new MockTwinStateProvider(states, hz);
  }

  start(onState: TwinListener, onProvenance?: ProvenanceListener): void {
    onProvenance?.(this.provenance);
    this.timer = setInterval(() => {
      const state = this.states[this.index % this.states.length];
      this.index += 1;
      if (state) onState(state);
    }, 1000 / this.hz);
  }

  stop(): void {
    if (this.timer !== undefined) clearInterval(this.timer);
    this.timer = undefined;
  }
}

/**
 * Live stream off `WS /twin/{scene_id}` -- the mock server today, the Isaac
 * bridge on the Spark later. Same route, same payload, so this class does not
 * change when the real thing arrives.
 */
export class WebSocketTwinStateProvider implements TwinStateProvider {
  readonly provenance: TwinProvenance = {
    label: "CONNECTING…",
    live: true,
    connected: false,
  };

  private socket: WebSocket | undefined;
  private onProvenance: ProvenanceListener | undefined;

  constructor(private readonly url: string) {}

  start(onState: TwinListener, onProvenance?: ProvenanceListener): void {
    this.onProvenance = onProvenance;
    this.socket = new WebSocket(this.url);

    this.socket.onopen = () => this.setProvenance("LIVE SIMULATION STATE", true, true);
    this.socket.onclose = () => this.setProvenance("SIMULATION DISCONNECTED", true, false);
    this.socket.onerror = () => this.setProvenance("SIMULATION UNREACHABLE", true, false);
    this.socket.onmessage = (event) => {
      try {
        onState(parseTwinState(String(event.data)));
      } catch (error) {
        // A malformed frame is dropped, not rendered. Better a frozen robot
        // than one showing a state the simulation never reported.
        console.warn("dropped malformed TwinState:", error);
      }
    };
  }

  stop(): void {
    this.socket?.close();
    this.socket = undefined;
  }

  private setProvenance(label: string, live: boolean, connected: boolean): void {
    this.provenance.label = label;
    this.provenance.live = live;
    this.provenance.connected = connected;
    this.onProvenance?.(this.provenance);
  }
}
