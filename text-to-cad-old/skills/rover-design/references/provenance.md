# Dimensional Provenance

Every physical constant is either **CONFIRMED** (read off a manufacturer
drawing or spec table) or **INFERRED** (derived, measured off a render, or taken
from a secondary source). The two are not interchangeable and must be labelled
at the point of use.

## Confirmed

- NEMA17 frame 42.3, bolt pitch 31.0 ±0.1, pilot Ø22.0, **pilot height 2.0**,
  M3 tapped 4.5 deep, shaft Ø5.0 × 24
- NEMA23 frame 56.4, bolt pitch 47.1 ±0.2, pilot Ø38.1, **pilot height 1.6**,
  **Ø5.0 clearance holes (not tapped)**, shaft Ø6.35 × 21
- 626ZZ 6 × 19 × 6, 608ZZ 8 × 22 × 7, both chamfer r 0.3 (SKF)
- 51106 thrust 30 × 47 × 11, chamfer 0.6 (SKF/NSK agree)
- Raspberry Pi 4B: 85 × 56, holes **Ø2.7** at 58 × 49 pitch, 3.5/3.5 corner
  inset, max component height 16.0 (official RPi drawing)
- Pololu A4988/DRV8825 carriers: 15.24 × 20.32, 2×8 header at 2.54, rows 12.70
  apart, 11.87 assembled, and **no mounting holes at all**
- DS3218 servo 40 × 20 × 40.4, ear span 54.5, hole pitch 49.5 × 10
- Planetary ratios 5.18:1 (≤1.5° backlash) and 26.851:1 (≤1°)

## Inferred — verify against a physical part

- Servo ear hole diameter: undimensioned on every drawing found
- Servo spline 25T / Ø5.8: a class figure, not a DSservo callout
- Servo spline offset 10.0 mm from body centre: measured off a rendered drawing
- 51106 individual washer heights: unpublished; a symmetric split is assumed
- 13.73:1 gearbox ratio: listed by a vendor as "14:1", drawing not opened

## Vendor CAD is for visuals only

Downloaded STEP files are placeholders. The Adafruit NEMA17 model measures body
32.65 / shaft 20.1 — a short 34 mm-class motor, not the 40 mm part it was
standing in for — and its corner geometry yields no clean 31.0 bolt pitch.

**Never cut a mating feature from a community CAD model.** Bolt patterns, pilot
bores, and shaft bores come from the datasheet constants in the catalogue.

## Known-bad secondary sources

- NopSCADlib gives the RPi4 mounting hole as Ø3.0. The official drawing says
  Ø2.7. Trust the drawing.
- "17HS4401" is a class name, not a precise part: 40, 42, 43, and 45 N·cm all
  appear under it across sellers.
