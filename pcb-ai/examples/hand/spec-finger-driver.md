# Hand servo driver — 5 finger channels

The board in the palm that drives one tendon actuator per finger. A tendon-driven hand
pulls each finger closed with a small geared servo, so this board fans a battery rail out
to five servos and passes five PWM signals through from the controller.

## Requirements

- Input: a 2-pin 2.54mm header carrying VBAT (6V nominal, 2S NiMH or a regulated pack)
  and GND. Expect 5 servos drawing 250mA each while moving and up to 900mA stalled, so
  size the input copper for 1.5A continuous.
- Five 3-pin 2.54mm servo headers, each carrying signal, VBAT and GND in that pin order.
- Signals arrive on a single 6-pin 2.54mm header from the controller: five PWM lines and
  a shared ground reference.
- Bulk capacitance on VBAT sized for five servos starting together — at least 220uF
  electrolytic — plus a 100nF ceramic close to the input header.
- A green LED with a series resistor showing the rail is live, drawing about 3mA.

## Constraints

- Board: 46mm x 30mm, 2 layers. It sits in the palm, so nothing may exceed 12mm tall.
- Input header on the west edge; the five servo headers along the east edge in a row so
  the tendon cables leave together.
- Signal header on the south edge, away from the servo power headers.
- Minimum trace width 0.4mm on VBAT: at 1.5A a thinner trace overheats.
