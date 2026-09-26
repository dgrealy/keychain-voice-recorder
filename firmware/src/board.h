// Board I/O: the latching button, the status LED and the battery voltage.
#pragma once

namespace board {

void init();                    // configure pins; call once after waking
bool buttonOn();                // debounced; true while the button is latched down
void led(bool on);
void blink(int times, int ms);  // ends with the LED off
float batteryVolts();           // NAN when no divider is fitted (pins.battery_adc = -1)

}  // namespace board
