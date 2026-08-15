/**
 * Room-scale ball pit: where everything sits, in struct_world.
 *
 * A deliberately different scene from `sortLayout.ts`, not a rescale of it.
 * That one is sized to what an SO-101 can physically reach -- a 48cm x 24cm
 * tabletop with 6cm balls -- because a robot shadow has to be able to follow
 * the human's hand for the demonstration to mean anything. This one is sized
 * to a *person*: balls you pick up with your whole hand, bins you turn and
 * reach toward, a floor you stand on.
 *
 * Nothing here is reachable by a 35cm desk arm, and that is the accepted
 * trade: this scene exists to be inhabited, not to be retargeted. The robot
 * is absent by design rather than present and silently unable to follow.
 *
 * Frame: right-handed, Z-up, meters. The human starts at the origin facing
 * +X, which is how a WebXR `local-floor` session places someone: origin on
 * the floor beneath the headset, and the scene laid out in front of them.
 */

import type { Vec3 } from "./contracts";

export type BallColor = "red" | "blue";

/**
 * Ball radius.
 *
 * 9cm (18cm across) is a real ball-pit ball: big enough to read instantly at
 * a distance, big enough that a whole-hand grab is the natural gesture rather
 * than a precise pinch, and far bigger than hand-tracking jitter. The desk
 * task's 3cm balls demand a careful pinch, which is exactly the fussiness
 * this scene is meant to avoid.
 */
export const BALL_RADIUS_M = 0.09;

/** Floor height. The human's own floor, since `local-floor` puts the session
 * origin on it. */
export const FLOOR_Z_M = 0;

/**
 * Where the balls start, scattered on the floor.
 *
 * In front of the human and slightly to either side -- close enough to walk
 * into within a normal guardian boundary, wide enough that sorting means
 * actually turning and moving rather than standing still.
 */
export const PIT_X_M: [number, number] = [0.5, 1.9];
export const PIT_Y_M: [number, number] = [-0.9, 0.9];

/** Waist-height bins, one per color, set out to either side so that sorting
 * is a whole-body motion. */
export const BIN_INTERIOR_M = 0.55;
export const BIN_HEIGHT_M = 0.45;
export const BIN_WALL_THICKNESS_M = 0.03;

export interface BinSpec {
  id: string;
  color: BallColor;
  /** Center of the interior floor, in struct_world. */
  center: Vec3;
}

export const BINS: readonly BinSpec[] = [
  { id: "red_bin", color: "red", center: [1.15, 1.35, FLOOR_Z_M] },
  { id: "blue_bin", color: "blue", center: [1.15, -1.35, FLOOR_Z_M] },
];

export interface BallSpec {
  id: string;
  color: BallColor;
  start: Vec3;
}

/** How many of each color. 12 each is enough to look like a pit and few
 * enough that clearing it is a finishable task rather than a chore. */
export const BALLS_PER_COLOR = 12;

/**
 * Deterministic scatter.
 *
 * A seeded generator rather than Math.random: two runs of the demo should
 * lay the balls out identically, so a recorded episode replays against the
 * scene it was recorded in, and so a bug is reproducible. Same reasoning as
 * the fixture pack's seed elsewhere in this repo.
 */
export const SCATTER_SEED = 20260815;

/** Mulberry32 -- small, fast, and good enough for scattering balls. */
function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function buildBalls(): BallSpec[] {
  const random = seededRandom(SCATTER_SEED);
  const balls: BallSpec[] = [];
  const placed: Vec3[] = [];
  // Balls must not start interpenetrating -- two spheres closer than a
  // diameter look broken and make a grab ambiguous. Rejection sampling with
  // a bounded retry: if the area ever gets too crowded to place one, the
  // count is wrong, and failing loudly beats silently overlapping.
  const minSeparation = BALL_RADIUS_M * 2.2;

  for (let i = 0; i < BALLS_PER_COLOR * 2; i += 1) {
    const color: BallColor = i % 2 === 0 ? "red" : "blue";
    let position: Vec3 | undefined;

    for (let attempt = 0; attempt < 400 && !position; attempt += 1) {
      const candidate: Vec3 = [
        PIT_X_M[0] + random() * (PIT_X_M[1] - PIT_X_M[0]),
        PIT_Y_M[0] + random() * (PIT_Y_M[1] - PIT_Y_M[0]),
        FLOOR_Z_M + BALL_RADIUS_M,
      ];
      const clear = placed.every(
        (other) =>
          Math.hypot(candidate[0] - other[0], candidate[1] - other[1]) >= minSeparation,
      );
      if (clear) position = candidate;
    }

    if (!position) {
      throw new Error(
        `could not place ball ${i} without overlap -- the pit is too small ` +
          `for ${BALLS_PER_COLOR * 2} balls of radius ${BALL_RADIUS_M}m`,
      );
    }

    placed.push(position);
    balls.push({
      id: `${color}_ball_${Math.floor(i / 2)}`,
      color,
      start: position,
    });
  }
  return balls;
}

export const BALLS: readonly BallSpec[] = buildBalls();

export const BALL_PIT_TASK_ID = "ball_pit_sort";

export function ballsOfColor(color: BallColor): BallSpec[] {
  return BALLS.filter((ball) => ball.color === color);
}

export function binFor(color: BallColor): BinSpec {
  const bin = BINS.find((b) => b.color === color);
  if (!bin) throw new Error(`no bin for color ${color}`);
  return bin;
}
