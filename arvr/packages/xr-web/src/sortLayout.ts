/**
 * Where the red/blue ball-sorting scene sits in struct_world.
 *
 * Every number here was measured against the real SO-101's kinematics, not
 * taken from the spec's round numbers. `tools/so101_reach_envelope.py` runs
 * forward kinematics off the committed URDF and a position-only damped
 * least-squares solve -- the same position-only choice `IkSolver` makes for
 * this arm, because a 5-DOF robot can only reach position and an arbitrary
 * orientation jointly on a lower-dimensional manifold.
 *
 * What it found, at table height z = 0.14 m:
 *
 *     x = 0.14 .. 0.38, |y| <= 0.24   every point converges under 5mm
 *     x = 0.34, y = 0.34              1.4cm short
 *     x = 0.42, y = 0.30              5.6cm short
 *     x = 0.50, y = 0.00              2.1cm short
 *
 * So the usable tabletop is about **0.48m wide by 0.24m deep**, not the
 * 0.60 x 0.45 the task spec suggests. 0.60 wide would put the baskets at
 * y = +/-0.30, which is already at the boundary, and 0.45 deep would need
 * x out past 0.55, which the arm cannot reach at any orientation. The spec
 * itself says to validate real reach and move objects rather than fake
 * reachability, so the scene is sized to the measurement and the deviation
 * is recorded here rather than discovered when IK starts failing mid-demo.
 *
 * Robot base is at struct_world origin. +X is away from the base (reach),
 * +Y is the human's left, +Z is up.
 */

import type { Vec3 } from "./contracts";

/** Tabletop surface height. Matches the button task's own table height, so
 * the two Spatial Teach scenes agree about where "the table" is. */
export const TABLE_Z_M = 0.14;

/** Reachable tabletop, measured (see module docstring). Used to keep balls
 * and baskets inside the arm's real envelope and to draw the table.
 *
 * Y reaches +/-0.26 rather than the +/-0.24 the dense sweep covered because
 * the boundary sweep found y = 0.30 still converging at x = 0.34, which is
 * where the baskets' outer corners sit. */
export const WORKSPACE_X_M: [number, number] = [0.14, 0.38];
export const WORKSPACE_Y_M: [number, number] = [-0.26, 0.26];
export const WORKSPACE_WIDTH_M = WORKSPACE_Y_M[1] - WORKSPACE_Y_M[0];
export const WORKSPACE_DEPTH_M = WORKSPACE_X_M[1] - WORKSPACE_X_M[0];

/** Spec's range is 0.025-0.035m; 0.03 is a ball a human can pinch without
 * the thumb and index tips touching (which would read as a closed gripper
 * regardless of the ball). */
export const BALL_RADIUS_M = 0.03;

/**
 * Basket interior side length.
 *
 * The spec asks for 0.16m. 0.15 is used because the far corner of a 0.16
 * basket centered where these are lands at (0.36, 0.26), which the reach
 * sweep puts right on the boundary -- and the robot has to follow the human
 * hand *over* the basket, not just to its center.
 */
export const BASKET_INTERIOR_M = 0.15;
export const BASKET_WALL_HEIGHT_M = 0.07;
export const BASKET_WALL_THICKNESS_M = 0.008;

export type BallColor = "red" | "blue";

export interface BasketSpec {
  id: string;
  color: BallColor;
  /** Center of the interior volume's floor, in struct_world. */
  center: Vec3;
}

export interface BallSpec {
  id: string;
  color: BallColor;
  /** Where the ball starts, center of the sphere. */
  start: Vec3;
}

const BALL_REST_Z = TABLE_Z_M + BALL_RADIUS_M;

/**
 * Baskets out to the sides, balls down the middle.
 *
 * The first layout put both baskets *behind* the balls at the same depth,
 * which read fine on paper and collided in the browser: a ball at the back
 * of the ball field overlapped a basket's near wall. Pushing the baskets to
 * the left and right instead leaves a clear center column for the balls,
 * and keeps every basket corner inside measured reach -- the outer corner
 * lands at (0.335, 0.25), and the boundary sweep converges at (0.34, 0.30).
 */
export const BASKETS: readonly BasketSpec[] = [
  { id: "red_basket", color: "red", center: [0.26, 0.175, TABLE_Z_M] },
  { id: "blue_basket", color: "blue", center: [0.26, -0.175, TABLE_Z_M] },
];

/**
 * Six balls in a 3x2 block down the center, interleaved by color.
 *
 * Interleaved rather than grouped so the demonstration alternates between
 * baskets -- six balls sorted left-then-right would produce two long
 * identical transports and a much less interesting episode.
 *
 * Spacing is 7cm and 9cm against a 6cm ball, so no two balls touch and no
 * single pinch is ambiguous between two of them (the grasp radius is 5cm).
 * The first version spaced them 4.5cm apart, which is less than one ball
 * diameter -- they were interpenetrating on screen.
 */
export const BALLS: readonly BallSpec[] = [
  { id: "red_ball_0", color: "red", start: [0.17, 0.045, BALL_REST_Z] },
  { id: "blue_ball_0", color: "blue", start: [0.17, -0.045, BALL_REST_Z] },
  { id: "blue_ball_1", color: "blue", start: [0.24, 0.045, BALL_REST_Z] },
  { id: "red_ball_1", color: "red", start: [0.24, -0.045, BALL_REST_Z] },
  { id: "red_ball_2", color: "red", start: [0.31, 0.045, BALL_REST_Z] },
  { id: "blue_ball_2", color: "blue", start: [0.31, -0.045, BALL_REST_Z] },
];

/** The shared task id every layer keys off (spec section 3). */
export const SORT_TASK_ID = "sort_red_blue_balls";

/** How many of each color there are, so the HUD does not hardcode 3. */
export function ballsOfColor(color: BallColor): BallSpec[] {
  return BALLS.filter((ball) => ball.color === color);
}

export function basketFor(color: BallColor): BasketSpec {
  const basket = BASKETS.find((b) => b.color === color);
  if (!basket) throw new Error(`no basket for color ${color}`);
  return basket;
}
