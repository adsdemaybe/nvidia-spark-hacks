/**
 * Grid, rasteriser and PDE solver shared by the thermal and power-integrity analyses.
 *
 * Both problems are the same equation on the same mesh:
 *
 *     ∇·(K ∇u) + s − a·u = 0
 *
 * Thermal: u is temperature, K is the sheet thermal conductance of the copper/FR4
 * stack [W/K per square], s is dissipated power per cell [W], and a is the convective
 * loss to ambient [W/K per cell].
 *
 * Power integrity: u is potential, K is the sheet electrical conductance of the
 * copper on one net [S per square], s is injected current per cell [A], a is zero,
 * and the supply pin is a Dirichlet cell.
 *
 * On a regular grid the conductance between two adjacent cells of a sheet is exactly
 * the sheet conductance — the h/h cancels — which is why one solver covers both.
 */
import { encodePng, colormap } from "./png.ts"

export interface Grid {
  nx: number
  ny: number
  /** Cell size in metres. */
  h: number
  /** Board extents in mm. */
  minX: number
  minY: number
  width: number
  height: number
}

export function makeGrid(board: { width: number; height: number }, cellMm = 0.15): Grid {
  const nx = Math.max(16, Math.ceil(board.width / cellMm))
  const ny = Math.max(16, Math.ceil(board.height / cellMm))
  return {
    nx,
    ny,
    h: (board.width / nx) * 1e-3,
    minX: -board.width / 2,
    minY: -board.height / 2,
    width: board.width,
    height: board.height,
  }
}

/** mm coordinate -> cell index. */
export function cellOf(g: Grid, x: number, y: number): [number, number] {
  return [
    Math.min(g.nx - 1, Math.max(0, Math.floor(((x - g.minX) / g.width) * g.nx))),
    Math.min(g.ny - 1, Math.max(0, Math.floor(((y - g.minY) / g.height) * g.ny))),
  ]
}

export function idx(g: Grid, i: number, j: number): number {
  return j * g.nx + i
}

/** Centre of cell (i,j) in mm. */
function centreOf(g: Grid, i: number, j: number): [number, number] {
  return [
    g.minX + ((i + 0.5) / g.nx) * g.width,
    g.minY + ((j + 0.5) / g.ny) * g.height,
  ]
}

/**
 * Half the cell diagonal. A feature is painted into every cell whose centre lies
 * within this much of it, which guarantees a thin trace still forms a *connected*
 * path on the grid — a broken path makes the electrical solve singular.
 */
function inclusionRadius(g: Grid): number {
  const cx = g.width / g.nx
  const cy = g.height / g.ny
  return Math.hypot(cx, cy) / 2
}

/**
 * Coverage fields take the maximum, not the sum: overlapping copper features do not
 * make a cell more than fully copper.
 */
function put(field: Float64Array, k: number, value: number) {
  if (value > field[k]) field[k] = value
}

/** Paint a filled axis-aligned rectangle (mm) into a coverage field. */
export function paintRect(
  g: Grid,
  field: Float64Array,
  cx: number,
  cy: number,
  w: number,
  h: number,
  value: number,
) {
  const [i0, j0] = cellOf(g, cx - w / 2, cy - h / 2)
  const [i1, j1] = cellOf(g, cx + w / 2, cy + h / 2)
  for (let j = j0; j <= j1; j++) {
    for (let i = i0; i <= i1; i++) put(field, idx(g, i, j), value)
  }
}

export function paintDisc(
  g: Grid,
  field: Float64Array,
  cx: number,
  cy: number,
  diameter: number,
  value: number,
) {
  const r = diameter / 2 + inclusionRadius(g)
  const [i0, j0] = cellOf(g, cx - r, cy - r)
  const [i1, j1] = cellOf(g, cx + r, cy + r)
  for (let j = j0; j <= j1; j++) {
    for (let i = i0; i <= i1; i++) {
      const [x, y] = centreOf(g, i, j)
      if ((x - cx) ** 2 + (y - cy) ** 2 <= r * r) put(field, idx(g, i, j), value)
    }
  }
}

/**
 * Paint a capsule: the swept region of a segment of the given width.
 *
 * Distance-to-segment rather than a chain of stamps, so the painted region is exactly
 * the routed copper dilated by half a cell — connected by construction, at any grid
 * resolution.
 */
export function paintSegment(
  g: Grid,
  field: Float64Array,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  width: number,
  value: number,
) {
  const r = width / 2 + inclusionRadius(g)
  const [i0, j0] = cellOf(g, Math.min(x0, x1) - r, Math.min(y0, y1) - r)
  const [i1, j1] = cellOf(g, Math.max(x0, x1) + r, Math.max(y0, y1) + r)
  const dx = x1 - x0
  const dy = y1 - y0
  const len2 = dx * dx + dy * dy

  for (let j = j0; j <= j1; j++) {
    for (let i = i0; i <= i1; i++) {
      const [x, y] = centreOf(g, i, j)
      const t = len2 > 0 ? Math.min(1, Math.max(0, ((x - x0) * dx + (y - y0) * dy) / len2)) : 0
      const px = x0 + dx * t
      const py = y0 + dy * t
      if ((x - px) ** 2 + (y - py) ** 2 <= r * r) put(field, idx(g, i, j), value)
    }
  }
}

/** Cells reachable from `seeds` through cells with positive conductance. */
export function reachable(g: Grid, conductance: Float64Array, seeds: number[]): Uint8Array {
  const seen = new Uint8Array(g.nx * g.ny)
  const queue: number[] = []
  for (const s of seeds) {
    if (conductance[s] > 0 && !seen[s]) {
      seen[s] = 1
      queue.push(s)
    }
  }
  for (let head = 0; head < queue.length; head++) {
    const k = queue[head]
    const i = k % g.nx
    const j = (k - i) / g.nx
    const neighbours = [
      i > 0 ? k - 1 : -1,
      i < g.nx - 1 ? k + 1 : -1,
      j > 0 ? k - g.nx : -1,
      j < g.ny - 1 ? k + g.nx : -1,
    ]
    for (const nk of neighbours) {
      if (nk < 0 || seen[nk] || conductance[nk] <= 0) continue
      seen[nk] = 1
      queue.push(nk)
    }
  }
  return seen
}

export interface SolveProblem {
  grid: Grid
  /** Sheet conductance per cell. Zero means the cell is not part of the domain. */
  conductance: Float64Array
  /** Source term per cell (W or A). */
  source: Float64Array
  /** Absorption term per cell (W/K); zero for the electrical problem. */
  absorption?: Float64Array
  /** Cells held at a fixed value, e.g. the supply pin. */
  dirichlet?: Map<number, number>
  maxIterations?: number
  tolerance?: number
}

/**
 * Preconditioned conjugate gradient on the matrix-free 5-point stencil.
 * The operator is symmetric positive definite once absorption or a Dirichlet cell
 * anchors it, which is true in both of our problems.
 */
export function solve(p: SolveProblem): { u: Float64Array; iterations: number; residual: number } {
  const { grid: g, conductance: K, source: b0 } = p
  const n = g.nx * g.ny
  const absorption = p.absorption ?? new Float64Array(n)
  const fixed = p.dirichlet ?? new Map<number, number>()
  const maxIterations = p.maxIterations ?? 4000
  const tolerance = p.tolerance ?? 1e-9

  /** Harmonic mean keeps a copper/no-copper boundary from leaking heat or current. */
  const link = (a: number, b: number) => (a > 0 && b > 0 ? (2 * a * b) / (a + b) : 0)

  const diag = new Float64Array(n)
  for (let j = 0; j < g.ny; j++) {
    for (let i = 0; i < g.nx; i++) {
      const k = idx(g, i, j)
      if (K[k] <= 0) {
        diag[k] = 1
        continue
      }
      let d = absorption[k]
      if (i > 0) d += link(K[k], K[k - 1])
      if (i < g.nx - 1) d += link(K[k], K[k + 1])
      if (j > 0) d += link(K[k], K[k - g.nx])
      if (j < g.ny - 1) d += link(K[k], K[k + g.nx])
      diag[k] = d > 0 ? d : 1
    }
  }

  /** y = A x */
  const apply = (x: Float64Array, out: Float64Array) => {
    for (let j = 0; j < g.ny; j++) {
      for (let i = 0; i < g.nx; i++) {
        const k = idx(g, i, j)
        if (K[k] <= 0 || fixed.has(k)) {
          out[k] = x[k]
          continue
        }
        let acc = absorption[k] * x[k]
        if (i > 0) acc += link(K[k], K[k - 1]) * (x[k] - x[k - 1])
        if (i < g.nx - 1) acc += link(K[k], K[k + 1]) * (x[k] - x[k + 1])
        if (j > 0) acc += link(K[k], K[k - g.nx]) * (x[k] - x[k - g.nx])
        if (j < g.ny - 1) acc += link(K[k], K[k + g.nx]) * (x[k] - x[k + g.nx])
        out[k] = acc
      }
    }
  }

  const b = new Float64Array(n)
  for (let k = 0; k < n; k++) b[k] = K[k] > 0 ? b0[k] : 0
  for (const [k, v] of fixed) b[k] = v

  const u = new Float64Array(n)
  for (const [k, v] of fixed) u[k] = v

  const r = new Float64Array(n)
  const Au = new Float64Array(n)
  apply(u, Au)
  for (let k = 0; k < n; k++) r[k] = b[k] - Au[k]

  const z = new Float64Array(n)
  for (let k = 0; k < n; k++) z[k] = r[k] / diag[k]
  const pv = Float64Array.from(z)
  const Ap = new Float64Array(n)

  let rz = 0
  for (let k = 0; k < n; k++) rz += r[k] * z[k]
  const b2 = Math.max(1e-30, b.reduce((s, v) => s + v * v, 0))

  let iterations = 0
  let residual = Math.sqrt(rz)
  for (; iterations < maxIterations; iterations++) {
    apply(pv, Ap)
    let pAp = 0
    for (let k = 0; k < n; k++) pAp += pv[k] * Ap[k]
    if (Math.abs(pAp) < 1e-300) break
    const alpha = rz / pAp
    for (let k = 0; k < n; k++) {
      u[k] += alpha * pv[k]
      r[k] -= alpha * Ap[k]
    }
    let rr = 0
    for (let k = 0; k < n; k++) rr += r[k] * r[k]
    residual = Math.sqrt(rr / b2)
    if (residual < tolerance) break

    for (let k = 0; k < n; k++) z[k] = r[k] / diag[k]
    let rzNew = 0
    for (let k = 0; k < n; k++) rzNew += r[k] * z[k]
    const beta = rzNew / rz
    rz = rzNew
    for (let k = 0; k < n; k++) pv[k] = z[k] + beta * pv[k]
  }

  return { u, iterations, residual }
}

/** Mean of a field over an axis-aligned region, ignoring cells outside the domain. */
export function averageOver(
  g: Grid,
  field: Float64Array,
  mask: Float64Array,
  cx: number,
  cy: number,
  w: number,
  h: number,
): number | undefined {
  const [i0, j0] = cellOf(g, cx - w / 2, cy - h / 2)
  const [i1, j1] = cellOf(g, cx + w / 2, cy + h / 2)
  let sum = 0
  let count = 0
  for (let j = j0; j <= j1; j++) {
    for (let i = i0; i <= i1; i++) {
      const k = idx(g, i, j)
      if (mask[k] <= 0) continue
      sum += field[k]
      count++
    }
  }
  return count ? sum / count : undefined
}

/**
 * The largest value of `field` under a component's own footprint.
 *
 * The companion to `averageOver`, and the one a junction limit should be compared
 * against. A chip is its own heat source, so the field under it is a peak with a skirt:
 * hottest under the die, cooling towards the edges of the package outline. Averaging
 * across the whole bounding box mixes the die temperature with that cooler edge copper
 * and reports a number the silicon never sees.
 *
 * Measured on rover-motor-driver, U1 at 0.300 W: the average reads 67.1 °C and the
 * hotspot 79.0 °C — 140 °C/W against 180 °C/W for the same package in the same solve.
 * The margin against a 150 °C limit was being computed from the lower one.
 *
 * This is deliberately the whole-footprint max rather than the value at the centre
 * point: a single cell is at the mercy of where the grid lines happen to fall, and the
 * max over the outline is stable under a change of resolution.
 */
export function maxOver(
  g: Grid,
  field: Float64Array,
  mask: Float64Array,
  cx: number,
  cy: number,
  w: number,
  h: number,
): number | undefined {
  const [i0, j0] = cellOf(g, cx - w / 2, cy - h / 2)
  const [i1, j1] = cellOf(g, cx + w / 2, cy + h / 2)
  let best: number | undefined
  for (let j = j0; j <= j1; j++) {
    for (let i = i0; i <= i1; i++) {
      const k = idx(g, i, j)
      if (mask[k] <= 0) continue
      if (best === undefined || field[k] > best) best = field[k]
    }
  }
  return best
}

export interface HeatmapLabel {
  x: number
  y: number
  text: string
}

/**
 * Render a scalar field as a labelled heatmap.
 *
 * The raster goes in as an embedded PNG and the annotations as real SVG, so the
 * reviewer sees component designators and a colour scale next to the field instead of
 * a bare gradient it has to guess at.
 */
export function renderHeatmap(args: {
  grid: Grid
  field: Float64Array
  mask: Float64Array
  title: string
  unit: string
  min?: number
  max?: number
  labels?: HeatmapLabel[]
}): string {
  const { grid: g, field, mask, title, unit } = args

  let lo = args.min ?? Infinity
  let hi = args.max ?? -Infinity
  if (args.min === undefined || args.max === undefined) {
    for (let k = 0; k < field.length; k++) {
      if (mask[k] <= 0) continue
      if (args.min === undefined) lo = Math.min(lo, field[k])
      if (args.max === undefined) hi = Math.max(hi, field[k])
    }
  }
  if (!isFinite(lo) || !isFinite(hi)) [lo, hi] = [0, 1]
  if (hi - lo < 1e-12) hi = lo + 1e-12

  const rgba = new Uint8Array(g.nx * g.ny * 4)
  for (let j = 0; j < g.ny; j++) {
    for (let i = 0; i < g.nx; i++) {
      const k = idx(g, i, j)
      // Flip vertically: PCB y grows upward, image rows grow downward.
      const px = ((g.ny - 1 - j) * g.nx + i) * 4
      if (mask[k] <= 0) {
        rgba.set([24, 24, 28, 255], px)
        continue
      }
      const [r, gc, b] = colormap((field[k] - lo) / (hi - lo))
      rgba.set([r, gc, b, 255], px)
    }
  }

  const dataUri = `data:image/png;base64,${encodePng(g.nx, g.ny, rgba).toString("base64")}`

  // Lay out in mm-space with a margin for the colour bar and its tick labels.
  const pad = 2
  const barW = 3
  const tickRoom = 12
  const titleRoom = 4
  const vbW = g.width + pad * 2 + barW + tickRoom
  const vbH = g.height + pad * 2 + titleRoom
  // Type is sized against the viewBox so it stays legible whatever the board size.
  const titleFont = vbW / 45
  const tickFont = vbW / 65
  const labelFont = vbW / 48

  const ticks = Array.from({ length: 5 }, (_, n) => {
    const t = n / 4
    const value = lo + (hi - lo) * (1 - t)
    const y = -g.height / 2 + t * g.height + tickFont * 0.35
    return `<text x="${g.width / 2 + pad + barW + 1}" y="${y}" font-size="${tickFont}" fill="#ddd">${value.toFixed(2)}</text>`
  }).join("")

  const gradientStops = Array.from({ length: 9 }, (_, n) => {
    const [r, gc, b] = colormap(1 - n / 8)
    return `<stop offset="${(n / 8) * 100}%" stop-color="rgb(${r},${gc},${b})"/>`
  }).join("")

  const labels = (args.labels ?? [])
    .map(
      (l) =>
        `<text x="${l.x}" y="${-l.y}" font-size="${labelFont}" fill="#fff" stroke="#000" stroke-width="${labelFont * 0.16}" paint-order="stroke" text-anchor="middle">${l.text}</text>`,
    )
    .join("")

  const vbX = -g.width / 2 - pad
  const vbY = -g.height / 2 - pad - titleRoom

  // No width/height attributes: the renderer scales from the viewBox, so the aspect
  // ratio is the board's rather than whatever an intrinsic size would impose.
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${vbX} ${vbY} ${vbW} ${vbH}">
  <defs><linearGradient id="scale" x1="0" y1="0" x2="0" y2="1">${gradientStops}</linearGradient></defs>
  <rect x="${vbX}" y="${vbY}" width="${vbW}" height="${vbH}" fill="#111"/>
  <text x="${-g.width / 2}" y="${-g.height / 2 - pad}" font-size="${titleFont}" fill="#fff">${title} [${unit}] — min ${lo.toFixed(3)}, max ${hi.toFixed(3)}</text>
  <image x="${-g.width / 2}" y="${-g.height / 2}" width="${g.width}" height="${g.height}" href="${dataUri}" style="image-rendering:pixelated"/>
  <rect x="${-g.width / 2}" y="${-g.height / 2}" width="${g.width}" height="${g.height}" fill="none" stroke="#888" stroke-width="0.15"/>
  ${labels}
  <rect x="${g.width / 2 + pad}" y="${-g.height / 2}" width="${barW}" height="${g.height}" fill="url(#scale)" stroke="#888" stroke-width="0.1"/>
  ${ticks}
</svg>`
}
