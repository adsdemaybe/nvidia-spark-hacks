# 3.3V dual-LED indicator board

A minimal breakout that plugs onto a host board and shows two status signals as LEDs.
Deliberately small: it exists to exercise the whole design loop end to end, not to be
clever.

## Requirements

- Input: a 2-pin 2.54mm header carrying 3.3V and GND. No regulator, no protection.
- Two indicator LEDs, each with its own series current-limiting resistor sized for
  roughly 5mA at 3.3V.
- Each LED is driven from its own signal pin on a 2-pin 2.54mm signal header, with the
  LED cathode to GND.
- One 100nF decoupling capacitor across the 3.3V rail.

## Constraints

- Board: 20mm x 15mm, 2 layers.
- Power header at one board edge, signal header at the opposite edge.
- Both LEDs in a row along one long edge so they are visible when installed.
- Every part on the top layer.
- Silkscreen designators on every part.
