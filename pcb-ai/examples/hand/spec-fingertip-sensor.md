# Fingertip tactile sensor

The smallest board in the hand: one per fingertip, reading contact force so the grasp
controller knows when it has the cup and how hard it is squeezing.

## Requirements

- A force-sensing resistor connects through a 2-pin 2.54mm header at the proximal edge.
- The FSR forms the lower leg of a divider against a fixed 10k resistor to 3.3V, so the
  divider output rises as contact force rises.
- Buffer that divider with a rail-to-rail op-amp in a unity-gain follower, so the cable
  back to the palm does not load the divider.
- A 100nF decoupling capacitor on the op-amp supply, placed within 2mm of its supply pin.
- Output leaves on a 3-pin 2.54mm header carrying 3.3V, the buffered signal, and GND.

## Constraints

- Board: 14mm x 10mm, 2 layers. It sits inside a fingertip, so nothing above 4mm tall.
- FSR header on the north edge, output header on the south edge, so the cable runs
  straight down the finger.
- Keep the divider node's copper short: it is high impedance until the buffer.
