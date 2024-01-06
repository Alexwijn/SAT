# Smart Autotune Thermostat

[![hacs][hacs-badge]][hacs-url]
[![release][release-badge]][release-url]
![build][build-badge]
[![discord][discord-badge]][discord-url]

<a href="https://www.buymeacoffee.com/alexwijn"><img src="https://img.buymeacoffee.com/button-api/?text=Buy me a coffee&emoji=&slug=alexwijn&button_colour=0ac982&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff"></a>

![opentherm-mqtt.png](https://raw.githubusercontent.com/Alexwijn/SAT/develop/.github/images/opentherm-mqtt.png)
![overshoot_protection.png](https://raw.githubusercontent.com/Alexwijn/SAT/develop/.github/images/overshoot_protection.png)


## What is the Smart Autotune Thermostat?

The Smart Autotune Thermostat, or SAT for short, is a custom component for Home Assistant that works with an [OpenTherm Gateway (OTGW)](https://otgw.tclcode.com/) (MQTT or Serial). It can also function as a PID ON/OFF thermostat, providing advanced temperature control based on Outside Temperature compensation and the Proportional-Integral-Derivative (PID) algorithm. Unlike other thermostat components, SAT supports automatic gain tuning and heating curve coefficients. This capability allows it to determine the optimal setpoint for your boiler without any manual intervention.

## Features
OpenTherm ( MQTT / Serial ):
- Multi-room temperature control with support for temperature synchronization for main climates
- Overshoot protection value automatic calculation mechanism
- Adjustable heating curve coefficients to fine-tune your heating system
- Target temperature step for adjusting the temperature in smaller increments
- Presets for different modes such as Away, Sleep, Home, Comfort
- Automatic gains for PID control
- PWM and Automatic-duty cycle
- Overshoot protection to prevent the boiler from overshooting the setpoint ( Low-Load Control )
- Climate valve offset to adjust the temperature reading for your climate valve
- Sample time for PID control to fine-tune your system's response time
- Open Window detection
- Control DHW setpoint

PID ON/OFF thermostat:

- Multi-room temperature control with support for temperature synchronization for main climates
- Adjustable heating curve coefficients to fine-tune your heating system
- Target temperature step for adjusting the temperature in smaller increments
- Presets for different modes such as Away, Sleep, Home, Comfort
- Automatic gains for PID control
- PWM and Automatic-duty cycle
- Climate valve offset to adjust the temperature reading for your climate valve
- Sample time for PID control to fine-tune your system's response time
- Open Window detection

## Installation
### Manual
1. Download the latest release of the SAT custom component from the GitHub repository.
2. Copy the sat directory to the custom_components directory in your Home Assistant configuration directory. If the custom_components directory doesn't exist, create it.
3. Restart Home Assistant to load the SAT custom component.
4. After installing the SAT custom component, you can configure it via the Home Assistant Config Flow interface.

### HACS
1. Install <a href="https://hacs.xyz/">HACS</a> if you haven't already.
2. Open the HACS web interface in Home Assistant and navigate to the Integrations section.
3. Click the three dots in the top-right corner and select "Custom repositories."
4. Enter the URL of the SAT custom component GitHub repository (https://github.com/Alexwijn/SAT) and select "Integration" as the category. Click "Add."
5. Once the SAT custom component appears in the list of available integrations, click "Install" to install it.
6. Restart Home Assistant to load the SAT custom component.
7. After installing the SAT custom component, you can configure it via the Home Assistant Config Flow interface.

# Configuration
SAT is configured using a config flow. After installation, go to the Integrations page in Home Assistant, click on the Add Integration button, and search for SAT if the autodiscovery feature fails.

## OpenTherm

1. OpenTherm Connection
   - MQTT
        - Name of the thermostat
        - Top Topic ( *MQTT Top Topic* found in OTGW-firmware Settings )
        - Device

   - Serial:
        - Name of the thermostat
        - URL

2. Configure sensors:
    - Inside Sensor Entity ( Your Room Temperature sensor )
    - Outside temperature sensor ( Your Outside Temperature sensor )

3. Heating System: Selecting the correct heating system type is important for SAT to accurately control the temperature and optimize performance. Choose the option that matches your setup to ensure proper temperature regulation throughout your home.

4. Calibrate System: Optimize your heating system by automatically determining the optimal PID values for your setup. When selecting Automatic Gains, please note that the system will go through a calibration process that may take approximately 20 minutes to complete.

If you already know this value, then use the "Manually enter the overshoot protection value" option and fill the value.

Automatic Gains are recommended for most users as it simplifies the setup process and ensures optimal performance. However, if you're familiar with PID control and prefer to manually set the values, you can choose to skip Automatic Gains.

Please note that choosing to skip Automatic Gains requires a good understanding of PID control and may require additional manual adjustments to achieve optimal performance.

## PID ON/OFF

To be completed

# Configure

## General tab:
*Maximum Setpoint*:
You can choose the max water setpoint for your system.
For radiator installations, it is recommended to choose a value between 55-75 °C.
For underfloor installations, the recommended max water setpoint is 50 °C.

Note for Radiators: Higher Max water setpoint values will cause a more aggressive warm-up.

*Heating Curve Coefficient*:
The heating curve coefficient is a configurable parameter in SAT that allows you to adjust the relationship between the outdoor temperature and the heating system output. This is useful for optimizing the heating system's performance in different weather conditions, as it allows you to adjust how much heat the system delivers as the outdoor temperature changes. By tweaking this parameter, you can achieve a more efficient and comfortable heating system.

## Areas tab:
*Multi-room setup*:
In multi-room mode, SAT monitors the climates in other rooms to determine the error and calculates how much heat is needed. It selects the highest error value as the error value for the current room, instead of using the average temperature across all rooms. This ensures that the temperature in each room is maintained at its desired level.

Note that SAT assumes that the climate control systems in the additional rooms are smart and won't exceed their target temperatures, as this can cause inefficiencies in the overall system. Once every climate control system in all rooms is around the target temperature, SAT can operate at its most efficient level.

*Contact Sensor*: You can add contact sensors to avoid wasting energy when a door/window is open. When the door/window is closed again, SAT restores heating.

## Presets tab:
Predefined temperature settings for different scenarios or activities.

# Terminology
*Heating Curve Coefficient*: By adjusting the heating curve coefficient, you can balance the heating loss of your home with the energy generated from your boiler at a given setpoint based on the outside temperature. When this value is properly tuned, the room temperature should hover around the setpoint.

*Gains*: SAT offers two ways of tuning the PID gains - manual and automatic.

- Manual tuning: You can fill the Proportional, Integral, and Derivative fields in the General tab with your values.
- Automatic Gains ( Recommended ): This option is enabled by default when the Overshoot protection value is present (During initial configuration). Automatic gains dynamically change the kP, kI, and kD values based on the heating curve value. So, based on the outside temperature, the gains change from mild to aggressive without intervention.

*Overshoot Protection*: This feature should be enabled when the minimum boiler capacity is greater than the control setpoint calculated by SAT. If the boiler overshoots the control setpoint, it may cycle, shortening the life of the burner. The solution is to adjust the boiler's on/off times to maintain the temperature at the setpoint while minimizing cycling.

Overshoot Protection Value (OPV) Calculation:
The OPV is a crucial value that determines the boiler's on/off times when the Overshoot Protection feature is present (During initial configuration).

*Manual Calculation*: If you know the maximum flow water temperature of the boiler at 0% modulation, you can fill in this value during the initial configuration.

*Automatic Calculation*: To calculate the OPV automatically, choose the "Calibrate and determine your overshoot protection value (approx. 20 min)" option during the initial configuration. SAT will then send the MM=0 and CS=75 commands, attempting to find the highest flow water temperature the boiler can produce while running at 0% modulation. This process takes at least 20 minutes. Once the OPV calculation is complete, SAT will resume normal operation and send a completion notification. The calculated value will be stored as an attribute in the SAT climate entity and used to determine the boiler's on/off times in the low-load control algorithm. If SAT detects that the boiler doesn't respect the 0% Max Modulation command, it will automatically change the calibration algorithm to a more sophisticated one to perform the calibration of the system.

Note: If you have any TRVs, open all of them (set them to a high setpoint) to ensure accurate calculation of the OPV. Once the calculation is complete, you can lower the setpoint back to your desired temperature.

*Automatic Duty Cycle*: When this option is enabled, SAT calculates the ON and OFF times of the boiler in 15-minute intervals, given that the kW needed to heat the home is less than the minimum boiler capacity. Moreover, using this feature, SAT can efficiently regulate the room temperature even in mild weather by automatically adjusting the duty cycle.

<!-- Badges -->

[hacs-url]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/hacs-default-orange.svg?style=for-the-badge
[release-badge]: https://img.shields.io/github/v/tag/Alexwijn/SAT?style=for-the-badge
[downloads-badge]: https://img.shields.io/github/downloads/Alexwijn/SAT/total?style=for-the-badge
[build-badge]: https://img.shields.io/github/actions/workflow/status/Alexwijn/SAT/pytest.yml?branch=develop&style=for-the-badge
[discord-badge]: https://img.shields.io/discord/1184879273991995515?label=Discord&logo=discord&logoColor=white&style=for-the-badge

<!-- References -->

[hacs]: https://hacs.xyz
[home-assistant]: https://www.home-assistant.io/
[release-url]: https://github.com/Alexwijn/SAT/releases
[discord-url]: https://discord.gg/jnVXpzqGEq