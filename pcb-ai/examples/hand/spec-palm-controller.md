# Palm controller

The board that turns commands into finger motion: it takes a serial link from the host,
generates five servo PWM channels, and reads five fingertip force signals.

## Requirements

- Input: a 4-pin 2.54mm header carrying 5V, GND, and a UART pair (host TX, host RX).
- An STM32F103 in LQFP48 as the controller, with its 8MHz crystal and the two 22pF load
  capacitors the oscillator needs.
- A 3.3V regulator from the 5V input, with a 10uF bulk capacitor on its input and output.
- Five PWM outputs leaving on a single 6-pin 2.54mm header: five signals and a ground,
  going to the servo driver board.
- Five analog inputs on a 6-pin 2.54mm header: five fingertip signals and a ground.
- One 100nF decoupling capacitor per MCU supply pin, each within 2mm of the pin it serves.
- A status LED with a series resistor on a spare GPIO, about 3mA.

## Constraints

- Board: 40mm x 34mm, 2 layers. It mounts in the palm behind the driver board, so nothing
  above 10mm tall.
- Host header on the west edge; the PWM header and the analog header both on the east
  edge, PWM above analog, so the two cable bundles stay separated.
- Keep the crystal and its load capacitors within 8mm of the MCU.
