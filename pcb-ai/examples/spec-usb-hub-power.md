# USB-C powered LED indicator board

A small utility board that takes USB-C bus power and drives three status LEDs from a
2.54mm header, so it can be plugged onto another board as a debug indicator.

## Requirements

- Input: USB-C receptacle, 5V bus power only (no data, no PD negotiation).
- Reverse-polarity and inrush protection on the 5V rail.
- Regulate 5V down to 3.3V, at least 300mA, using an LDO in a SOT-223 or SOT-23-5
  footprint. Bulk and bypass capacitance on both sides of the regulator.
- Three indicator LEDs, each with its own current-limiting resistor sized for ~5mA
  at 3.3V, driven from a 4-pin 2.54mm header (3 signals + ground).
- A power LED on the 3.3V rail.
- A 2-pin test point pair exposing 3.3V and GND.

## Constraints

- Board: 30mm x 20mm, 2 layers.
- USB-C connector at one board edge, the 2.54mm header at the opposite edge.
- Indicator LEDs in a row along one long edge so they are visible when installed.
- Every part on the top layer.
- Silkscreen designators on every part.
