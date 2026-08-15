/**
 * The mug-pickup task: where the mug is, whether it is held, and whether the
 * demonstration succeeded.
 *
 * Free of three.js and of any hand-tracking type, so the rule that decides
 * what enters the training set is testable without a camera or a headset.
 * That separation is the point: "did this demonstration succeed" must be a
 * deterministic geometric fact, never a judgement call, because it gates what
 * a policy learns from.
 *
 * The physics is minimal on purpose. A mug that is picked up, carried and put
 * down needs gravity and a table to rest on -- and nothing more. Authoritative
 * contact and dynamics belong to the simulator at verification time, not to
 * the capture client.
 */

import {
  MUG_ID,
  MUG_START,
  LIFT_THRESHOLD_Z_M,
  TABLE_DROP_M,
  TABLE_X_M,
  TABLE_Y_M,
  TABLE_Z_M,
} from "./mugPickupLayout";
import type { Vec3 } from "./contracts";

export type MugEventType =
  | "grasp_start"
  | "grasp_end"
  | "lifted"
  | "placed"
  | "task_success"
  | "tracking_lost";

export interface MugEvent {
  type: MugEventType;
  objectId?: string;
}

const GRAVITY_M_S2 = 9.81;

export class MugPickupTask {
  private position: Vec3 = [...MUG_START] as Vec3;
  private velocityZ = 0;
  private heldFlag = false;
  private liftedFlag = false;
  private successAnnounced = false;
  readonly events: MugEvent[] = [];

  reset(): void {
    this.position = [...MUG_START] as Vec3;
    this.velocityZ = 0;
    this.heldFlag = false;
    this.liftedFlag = false;
    this.successAnnounced = false;
    this.events.length = 0;
  }

  mugPosition(): Vec3 {
    return [...this.position] as Vec3;
  }

  get isHeld(): boolean {
    return this.heldFlag;
  }

  /** True once the mug has been lifted clear of the table at any point. This
   * latches: setting it down again does not un-demonstrate the pickup. */
  get wasLifted(): boolean {
    return this.liftedFlag;
  }

  /**
   * The base of the mug, which is what "clear of the table" is measured on.
   *
   * The generated asset's local origin is the centre of its base, so the
   * tracked position already IS the base and no half-height arithmetic
   * applies. The previous stand-in cylinder was centre-origin, and carrying
   * that assumption over would have read a mug standing on the table as
   * already lifted by half its height.
   */
  get baseZ(): number {
    return this.position[2];
  }

  setMugPosition(position: Vec3): void {
    this.position = [...position] as Vec3;
    this.velocityZ = 0;
  }

  setHeld(held: boolean): void {
    if (this.heldFlag === held) return;
    this.heldFlag = held;
    this.events.push({ type: held ? "grasp_start" : "grasp_end", objectId: MUG_ID });
  }

  markTrackingLost(): void {
    this.events.push({ type: "tracking_lost" });
  }

  /** Advance the mug if it is falling, then re-evaluate the predicate. */
  step(dtSeconds: number): void {
    if (!this.heldFlag) {
      this.velocityZ -= GRAVITY_M_S2 * dtSeconds;
      const nextZ = this.position[2] + this.velocityZ * dtSeconds;
      const restZ = this.restingCentreZ();
      if (nextZ <= restZ) {
        this.position = [this.position[0], this.position[1], restZ];
        this.velocityZ = 0;
      } else {
        this.position = [this.position[0], this.position[1], nextZ];
      }
    }
    this.settle();
  }

  /** Re-evaluate lift/success. Split out from `step` so a test can assert the
   * predicate without simulating a fall. */
  settle(): void {
    if (!this.liftedFlag && this.heldFlag && this.baseZ >= LIFT_THRESHOLD_Z_M) {
      this.liftedFlag = true;
      this.events.push({ type: "lifted", objectId: MUG_ID });
    }
    if (this.liftedFlag && !this.heldFlag && this.isResting()) {
      // Put back down after a real lift. Recorded because "picked it up and
      // set it down" is a complete demonstration, not an abandoned one.
      if (!this.events.some((e) => e.type === "placed")) {
        this.events.push({ type: "placed", objectId: MUG_ID });
      }
    }
    if (this.isSuccess && !this.successAnnounced) {
      this.successAnnounced = true;
      this.events.push({ type: "task_success", objectId: MUG_ID });
    }
  }

  /**
   * The demonstration succeeded: the mug was lifted clear of the table.
   *
   * Deliberately not "and put back down" -- the skill being taught is the
   * pickup, and requiring a placement would reject a perfectly good grasp
   * demonstration for a reason unrelated to what the policy learns.
   */
  get isSuccess(): boolean {
    return this.liftedFlag;
  }

  /** Ball-and-basket style object record for the episode. One object, but the
   * shape matches what the recorder and exporter already consume. */
  objectStates(): Array<{ id: string; position_m: Vec3 }> {
    return [{ id: MUG_ID, position_m: this.mugPosition() }];
  }

  /** Where the mug's centre rests: on the table if it is over it, otherwise on
   * the floor. A mug knocked off the edge should fall to the ground rather
   * than hover at table height. */
  private restingCentreZ(): number {
    const overTable =
      this.position[0] >= TABLE_X_M[0] &&
      this.position[0] <= TABLE_X_M[1] &&
      this.position[1] >= TABLE_Y_M[0] &&
      this.position[1] <= TABLE_Y_M[1];
    return overTable ? TABLE_Z_M : -TABLE_DROP_M;
  }

  private isResting(): boolean {
    return Math.abs(this.position[2] - this.restingCentreZ()) < 1e-4;
  }
}
