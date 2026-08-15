---
name: robot-from-image
description: Recreate a parametric robot design from a reference image — a generated concept render, a photo, or a sketch — and validate the CAD against it by silhouette comparison. Use when a robot design is specified visually rather than numerically, when checking whether a built model matches a concept image, or when converting a picture into buildable design variables. Use $rover-design to refine the fitted design against physics criteria; use $sim-export to put it in a simulator.
---

# Robot From Image

Provenance: maintained in this workspace under `skills/robot-from-image/`.

You are the vision model. This skill does not call an image API — it tells you
how to read a reference image and what structured output to produce, and gives
you deterministic scripts for the parts that must not be guessed.

## Core Rules

1. **An image has no scale.** It constrains ratios, topology, and outline. It
   never gives millimetres. Emit ratios; supply an absolute size only as an
   explicit `absolute_hint_mm`, and label it as an assumption you introduced.
   See `references/what-images-cannot-tell-you.md`.
2. **Do not invent precision.** "The chassis looks about twice as long as it is
   wide" is honest. "chassis_w_over_chassis_l = 0.4873" is fabrication.
   Two significant figures is the most an image supports.
3. **Report topology separately from proportion.** Wheel count, number of arm
   links, drive type, and gripper kind are discrete facts you either can or
   cannot read. Say which you could not determine.
4. **Never let the picture overrule physics.** `scripts/fit` clamps ratios to
   the buildable range and reports every conflict. A conflict means the image
   depicts something this design space cannot build — surface it, do not
   quietly absorb it.
5. **Validate by measurement, not by eye.** After fitting, run
   `scripts/silhouette` and report the IoU. Do not claim a match because the
   render "looks like" the reference.
6. **A high IoU is not correctness.** Silhouettes are outlines. Two designs with
   identical outlines can differ completely in mechanism, mass, and whether
   anything actually works. Hand off to `$rover-design` for that.

## Workflow

1. Look at the image. Write down, before measuring anything: what kind of robot,
   how many wheels, how many arm links, what the end effector appears to be, and
   what you cannot determine.
2. Estimate proportions as ratios against one reference dimension — normally
   chassis length. Two significant figures.
3. Fit: `scripts/fit --spec '<json>'`. Read the conflicts. Conflicts are the
   most informative output, not a nuisance.
4. Compare: `scripts/silhouette --reference <image> --sweep`. The reference's
   camera is unknown, so sweep rather than assuming a viewpoint.
5. If the IoU is weak, the disagreement is either proportion (adjust ratios) or
   topology (the model cannot represent it — say so). Distinguish the two.
6. Hand the fitted design to `$rover-design` to make it physically valid. Fitting
   an image does not produce a working robot; it produces a starting point.

## Spec format

```json
{
  "wheel_count": 4,
  "arm_links": 2,
  "end_effector": "parallel jaw gripper",
  "undetermined": ["drive type", "whether the turntable rotates"],
  "absolute_hint_mm": {"chassis_l": 240},
  "ratios": {
    "wheel_d_over_chassis_l": 0.30,
    "chassis_w_over_chassis_l": 0.55,
    "link1_over_chassis_l": 0.52,
    "link2_over_chassis_l": 0.42,
    "axle_frac": 0.40
  }
}
```

## Commands

```bash
python scripts/fit --spec '<json>'
python scripts/fit --spec '<json>' --format json
python scripts/silhouette --reference concept.png --sweep
python scripts/silhouette --reference concept.png --azimuth -60 --elevation 24
python scripts/silhouette --render-only mask.png --azimuth -60 --elevation 24
```

`silhouette` exits nonzero below IoU 0.62. `fit` exits nonzero if any criterion
fails or any ratio conflicted.

## Calibration

Measured on this model: a design compared against its own rendered silhouette
scores **IoU 0.90** (capped by the sweep grid, not by the method), and a
deliberately wrong design — longer chassis, bigger wheels, shorter arm — scores
**0.60**. So treat 0.80+ as a strong match, 0.62–0.80 as plausible, and below
0.62 as a real disagreement worth investigating. The gap between right and wrong
is roughly 0.30 IoU; anything claiming finer discrimination than that is noise.
