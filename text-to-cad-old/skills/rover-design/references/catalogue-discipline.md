# Catalogue Discipline

The single most important rule in this skill: **an optimiser will exploit any
variable you do not constrain to real parts.**

## What went wrong once already

`SHOULDER_GEAR` was originally a continuous variable bounded `(1.0, 27.0)`. The
search converged on **7.5:1**, every criterion passed, and the design was
unbuildable. Real planetary reductions for this motor class are 5.18:1, 13.73:1,
and 26.851:1. Nothing sells 7.5:1.

The fix was not a better optimiser. It was making the variable discrete and
sourcing the real ratios, with their real backlash, from vendor drawings.

## The rule

- A motor is a key from `scripts/catalogue`. There are no other motors.
- A gear ratio is a member of the listed ratios. There are no other ratios.
- If the design needs a part that is not in the catalogue, the correct action is
  to **source it, verify it against a dimensioned drawing, and add it** — not to
  relax the constraint.

## Adding a part

A catalogue entry needs, from an actual dimensioned drawing:

frame, body length, bolt pitch, pilot diameter, pilot **height**, shaft diameter
and length, mass, holding torque, hole diameter, and whether holes are tapped or
clearance.

Two of those are routinely wrong in secondary sources:

- **Pilot boss height** is rarely dimensioned. NEMA17 is 2.0 mm and NEMA23 is
  1.6 mm, both confirmed on vendor drawings — not inferred.
- **NEMA23 mounting holes are Ø5.0 clearance, bolted through.** They are *not*
  tapped like NEMA17's M3. A design that taps them will not assemble.

## Backlash is a real cost

A gearbox is not free torque. The 5.18:1 unit has ≤1.5° backlash; over a 277 mm
reach that is roughly 7 mm of end-effector slop. The `backlash` criterion exists
so this trade is measured rather than ignored, and once it existed, both search
routes rejected gearing in favour of a direct-drive NEMA23.
