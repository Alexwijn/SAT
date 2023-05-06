# Smart Autotune Thermostat (SAT)
The Smart Autotune Thermostat (SAT) is a custom component for Home Assistant that works with an [OpenTherm Gateway (OTGW)](https://otgw.tclcode.com/) in order to provide advanced temperature control functionality based on an advanced Outside Temperature compensation and Proportional-Integral-Derivative (PID) algorithm. Unlike other thermostat components, SAT supports automatic gain tuning and heating curve coefficient, which means it can determine the optimal setpoint for your boiler without any manual intervention.

## Features
- Multi-room temperature control with support for temperature synchronization for main climates
- Adjustable heating curve coefficients to fine-tune your heating system
- Target temperature step for adjusting the temperature in smaller increments
- Presets for different modes such as Away, Sleep, Home, Comfort
- Automatic gains for PID control
- PWM and Automatic duty cycle
- Overshoot protection to prevent the boiler from overshooting the setpoint
- Climate valve offset to adjust the temperature reading for your climate valve
- Sample time for PID control to fine-tune your system's response time
- Control DHW setpoint

## Installation
### Manual
1. Download the latest release of the SAT custom component from the GitHub repository.
2. Copy the sat directory to the custom_components directory in your Home Assistant configuration directory. If the custom_components directory doesn't exist, create it.
3. Restart Home Assistant to load the SAT custom component.
4. After installing the SAT custom component, you can configure it via the Home Assistant Config Flow interface.

### HACS
1. Install <a href="https://hacs.xyz/">HACS</a> if you haven't already.
2. Open the HACS web interface in Home Assistant and navigate to the Integrations section.
3. Click the three dots in the top-right corner and select "Custom repositories".
4. Enter the URL of the SAT custom component GitHub repository (https://github.com/Alexwijn/SAT) and select "Integration" as the category. Click "Add".
5. Once the SAT custom component appears in the list of available integrations, click "Install" to install it.
6. Restart Home Assistant to load the SAT custom component.
7. After installing the SAT custom component, you can configure it via the Home Assistant Config Flow interface.

## Configuration
SAT is configured using a config flow. After installation, go to the Integrations page in Home Assistant, click on the Add Integration button, and search for SAT. Follow the prompts to configure the integration.

## Multi-room setup
In multi-room mode, SAT monitors the climates in other rooms to determine the error and calculates how much heat is needed. It selects the highest error value as the error value for the current room, instead of using the average temperature across all rooms. This ensures that the temperature in each room is maintained at its desired level.

Note that SAT assumes that the climate control systems in the additional rooms are smart and won't exceed their target temperatures, as this can cause inefficiencies in the overall system. Once every climate control system in all rooms is around the target temperature, SAT can operate at its most efficient level.

## Heating Curve Coefficient
The heating curve coefficient is a configurable parameter in SAT that allows you to adjust the relationship between the outdoor temperature and the heating system output. This is useful for optimizing the heating system's performance in different weather conditions, as it allows you to adjust how much heat the system delivers as the outdoor temperature changes. By tweaking this parameter, you can achieve a more efficient and comfortable heating system.

## Automatic gains
SAT supports automatic PID gain tuning. When this feature is enabled, SAT will continuously adjust the PID gains to optimize the temperature control performance based on the current conditions instead of manually filling in the PID-values.

## Overshoot protection
With overshoot protection enabled, SAT will automatically calculate the maximum allowed modulation value for the boiler based on the setpoint and the calculation overshoot protection value.

## Tuning
*Heating Curve Coefficient*: By adjusting the heating curve coefficient, you can balance the heating loss of your home with the energy generated from your boiler at a given setpoint based on the outside temperature. When this value is properly tuned then the room temperature should float around the setpoint.

*Gains*: SAT offers two ways of tuning the PID gains - manual and automatic.
- Manual tuning: You can fill the Proportional, Integral and Derivative fields in the General tab with your own values.
- Automatic Gains ( Recommended ): You can enable this option in the Advanced Tab. Automatic gains dynamically change the kP, kI and kD values based on the heating curve value. So, based on the outside temperature the gains are changing from mild to aggressive without intervention.

*Overshoot Protection* ( Experimental ): When this option is enabled, SAT sends the MM=0 and CS=75 commands. Then SAT tries to find the highest boiler flow water temperature that can be produced given that the boiler runs at 0 % modulation. This mechanism needs at least 20 minutes. When overshoot protection value calculation is over, SAT falls back to it's normal operation. This value is stored as attribute in the SAT climate entity and then is used in order to calculate the boiler ON/OFF times of the low load control algorithm.

*Automatic Duty Cycle* ( Experimental ): When this option is enabled, SAT calculates the ON and OFF times of the boiler, in 15 minutes intervals, given that the kW needed to heat the home are less than the minimun boiler capacity. Moreover using this feature SAT is able to regulate efficiently the room temperature even in mild weather by automatically adjusting the duty cycle.

## Support
If you want to support this project, you can [**buy me a coffee here**](https://www.buymeacoffee.com/alexwijn).

<a href="https://www.buymeacoffee.com/alexwijn"><img src="https://img.buymeacoffee.com/button-api/?text=Buy me a coffee&emoji=&slug=alexwijn&button_colour=0ac982&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff"></a>
