/**
 * FOLLOW mode's connection to ar_backend's Follow stream (spec section 39,
 * acceptance gate section 64).
 *
 * This client owns computing `follow_target` (spatial.ts) and nothing
 * about how the robot gets there (spec section 24) — this module just
 * carries FollowState out and TwinState back over one WebSocket per
 * session. STOP closes the socket outright rather than sending a "stop"
 * message: there is then nothing left that could keep producing motion,
 * which is the literal acceptance-gate requirement ("no queue to drain").
 */

import type { FollowState, TwinState } from "./contracts";
import { parseTwinState } from "./contracts";

export type FollowSessionState = "connecting" | "open" | "closed";

export class FollowSession {
  private socket: WebSocket | undefined;
  state: FollowSessionState = "closed";

  private constructor(
    private readonly apiBase: string,
    readonly sessionId: string,
  ) {}

  static async start(apiBase: string): Promise<FollowSession> {
    const response = await fetch(`${apiBase}/xr/follow`, { method: "POST" });
    if (!response.ok) {
      throw new Error(`POST /xr/follow -> ${response.status}`);
    }
    const { session_id: sessionId } = (await response.json()) as { session_id: string };
    return new FollowSession(apiBase, sessionId);
  }

  /** Opens the socket. Safe to call once; sending before this resolves is a no-op. */
  connect(onTwinState: (state: TwinState) => void, onClose?: () => void): void {
    const wsBase = this.apiBase.replace(/^http/, "ws");
    this.state = "connecting";
    this.socket = new WebSocket(`${wsBase}/xr/follow/${this.sessionId}`);
    this.socket.onopen = () => { this.state = "open"; };
    this.socket.onclose = () => { this.state = "closed"; onClose?.(); };
    this.socket.onerror = () => { this.state = "closed"; };
    this.socket.onmessage = (event) => {
      try {
        onTwinState(parseTwinState(String(event.data)));
      } catch (error) {
        console.warn("dropped malformed TwinState on Follow stream:", error);
      }
    };
  }

  /** Sends one FollowState update. A no-op while paused/not yet open — the
   * caller controls the send cadence, this just guards the socket state. */
  send(state: FollowState): void {
    if (this.state !== "open" || !this.socket) return;
    this.socket.send(JSON.stringify(state));
  }

  /** Closes the socket. Nothing after this can produce further motion. */
  stop(): void {
    this.socket?.close();
    this.socket = undefined;
    this.state = "closed";
  }
}
