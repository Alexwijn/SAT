# Smart Autotune Thermostat (SAT)

[![hacs][hacs-badge]][hacs-url]
[![release][release-badge]][release-url]
![build][build-badge]
[![discord][discord-badge]][discord-url]

<a href="https://www.buymeacoffee.com/alexwijn"><img src="https://img.buymeacoffee.com/button-api/?text=Buy me a coffee&emoji=&slug=alexwijn&button_colour=0ac982&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff"></a>

![OpenTherm MQTT Integration](https://raw.githubusercontent.com/Alexwijn/SAT/develop/.github/images/opentherm-mqtt.png)
![Overshoot Protection Graph](https://raw.githubusercontent.com/Alexwijn/SAT/develop/.github/images/overshoot_protection.png)

## Overview

The **Smart Autotune Thermostat (SAT)** is a custom component for [Home Assistant][home-assistant] designed to optimize your heating system's performance. It integrates with an [OpenTherm Gateway (OTGW)](https://otgw.tclcode.com/) via MQTT or Serial connection, and can also function as a PID ON/OFF thermostat. SAT provides advanced temperature control using Outside Temperature Compensation and Proportional-Integral-Derivative (PID) algorithms. Unlike other thermostat components, SAT supports automatic gain tuning and heating curve coefficients, allowing it to determine the optimal setpoint for your boiler without manual intervention.

## Features

- Multi-room temperature control with support for temperature synchronization for main climates.
- Adjustable heating curve coefficients to fine-tune your heating system.
- Target temperature step for adjusting the temperature in smaller increments.
- Presets for different modes such as Away, Sleep, Home, Comfort.
- Automatic gains for PID control.
- PWM and automatic duty cycle.
- Climate valve offset to adjust the temperature reading for your climate valve.
- Sample time for PID control to fine-tune your system's response time.
- Open window detection.

### OpenTherm-Specific Features

- Overshoot protection value automatic calculation mechanism.
- Overshoot protection to prevent the boiler from overshooting the setpoint (Low-Load Control).
- Control Domestic Hot Water (DHW) setpoint.

## Installation

### HACS

Smart Autotune Thermostat (SAT) is available in [HACS][hacs] (Home Assistant Community Store).

Use this link to directly go to the repository in HACS:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Alexwijn&repository=SAT)

**Or follow these steps:**

1. Install HACS if you don't have it already.
2. Open HACS in Home Assistant.
3. Search for **Smart Autotune Thermostat**.
4. Click the **Download** button. ⬇️

### Manual Installation

1. Download the latest release of the SAT custom component from the [GitHub repository][release-url].
2. Copy the `sat` directory to the `custom_components` directory in your Home Assistant configuration directory. If the `custom_components` directory doesn't exist, create it.
3. Restart Home Assistant to load the SAT custom component.
4. After installing the SAT custom component, you can configure it via the Home Assistant Config Flow interface.

## Configuration

SAT is configured using a config flow. After installation, go to the **Integrations** page in Home Assistant, click on the **Add Integration** button, and search for **SAT** if the autodiscovery feature fails.

### OpenTherm Configuration

1. **OpenTherm Connection**

   - **MQTT**:
     - **Name of the thermostat**
     - **Top Topic** (*MQTT Top Topic* found in OTGW-firmware Settings)
     - **Device**

   - **Serial**:
     - **Name of the thermostat**
     - **URL**

2. **Configure Sensors**

   - **Inside Temperature Sensor** (Your room temperature sensor)
   - **Outside Temperature Sensor** (Your outside temperature sensor)
   - **Inside Humidity Sensor** (Your room humidity sensor)

3. **Heating System**

   Selecting the correct heating system type is crucial for SAT to accurately control the temperature and optimize performance. Choose the option that matches your setup (e.g., Radiators or Underfloor heating) to ensure proper temperature regulation throughout your home.

4. **Multi-Room Setup**

   > **Note:** If SAT is the only climate entity, skip this step.

   - **Primary:** You can add your physical thermostat. SAT will synchronize the `hvac_action` of the physical thermostat with the SAT climate entity's `hvac_action`. Additionally, the physical thermostat will act as a backup if any failure to Home Assistant occurs.
   - **Rooms:** You can add your TRV (Thermostatic Radiator Valve) climate entities. When any of the rooms request heating, SAT will start the boiler.

   > **Tip:** Refer to the **Heating Mode** setting in the **General** tab for further customization.

5. **Calibrate System**

   Optimize your heating system by automatically determining the optimal PID values for your setup. When selecting **Automatic Gains**, please note that the system will go through a calibration process that may take approximately 20 minutes to complete.

   If you already know this value, use the **Manually enter the overshoot protection value** option and enter the value.

   Automatic Gains are recommended for most users as it simplifies the setup process and ensures optimal performance. However, if you're familiar with PID control and prefer to manually set the values, you can choose to skip Automatic Gains.

   > **Note:** Choosing to skip Automatic Gains requires a good understanding of PID control and may require additional manual adjustments to achieve optimal performance.

### PID ON/OFF Thermostat Configuration

_To be completed._

## Settings

### General Tab

**Heating Curve Version**

Represents the formulas used for calculating the heating curve. The available options are:

- **Radiators**:
  - [Classic Curve](https://www.desmos.com/calculator/cy8gjiciny)
  - [Quantum Curve](https://www.desmos.com/calculator/hmrlrapnxz)
  - [Precision Curve](https://www.desmos.com/calculator/spfvsid4ds) (**Recommended**)

- **Underfloor Heating**:
  - [Classic Curve](https://www.desmos.com/calculator/exjth5qsoe)
  - [Quantum Curve](https://www.desmos.com/calculator/ke69ywalcz)
  - [Precision Curve](https://www.desmos.com/calculator/i7f7uuyaoz) (**Recommended**)

> **Note:** Graph parameters:
>
> - `a`: Heating Curve Coefficient
> - `b`: Room Setpoint

> **Tip:** You can add the graph as an `iframe` card in Home Assistant for easy reference.

**Example:**

```yaml
type: iframe
url: https://www.desmos.com/calculator/spfvsid4ds
allow_open_top_navigation: true
allow: fullscreen
aspect_ratio: 130%
```

**PID Controller Version**

- **Classic Controller**
- **Improved Controller**

**Heating Mode**

> **Note:** Available only for multi-room installations.

- **Comfort:** SAT monitors the climates in other rooms to determine the error. It selects the highest error value as the PID error value for the current room.
- **Eco:** SAT monitors **only** the main thermostat's error, which is used as the PID error.

**Maximum Setpoint**

Set the maximum water setpoint for your system.

- **Radiators:** Recommended to choose a value between 55–75 °C. Higher values will cause a more aggressive warm-up.
- **Underfloor Heating:** Recommended maximum water setpoint is 50 °C.

**Heating Curve Coefficient**

Adjust the heating curve coefficient to balance the heating loss of your home with the energy generated from your boiler based on the outside temperature. Proper tuning ensures the room temperature hovers around the setpoint.

**Automatic Gains Value**

Automatically tweak the aggressiveness of the PID gains (`kP`, `kI`, and `kD` values). Best results are achieved when using the same value as the Heating Curve Coefficient.

**Derivative Time Weight**

Further tweak the `kD` value. A good starting value is `2`.

**Adjustment Factor for Return Temperature**

This factor adjusts the heating setpoint based on the boiler's return temperature, affecting heating responsiveness and efficiency. A higher value increases sensitivity to temperature changes, enhancing control over comfort and energy use.

> **Tip:** Recommended starting range is `0.1` to `0.5`. Adjust to suit your system and comfort preferences.

**Contact Sensor**

Add contact sensors (e.g., door/window sensors) to avoid wasting energy when a door/window is open. When the door/window is closed again, SAT restores heating.

### Presets Tab

Predefined temperature settings for different scenarios or activities, such as Away, Sleep, Home, and Comfort.

### Advanced Tab

**Thermal Comfort**

Uses the Summer Simmer Index as the temperature sensor. The Summer Simmer Index refers to the perceived temperature based on the measured air temperature and relative humidity.

**Dynamic Minimum Setpoint (Experimental)**

In multi-room installations, the boiler flow water temperature may exceed the Overshoot Protection Value during Low-Load Control (some valves may be closed). This mechanism monitors the boiler return water temperature and adjusts the Control Setpoint sent to the boiler accordingly. See **Adjustment Factor for Return Temperature**.

**Minimum Consumption**

Find this value in your boiler's manual. SAT uses this value to calculate the instant gas consumption.

**Maximum Consumption**

Find this value in your boiler's manual. SAT uses this value to calculate the instant gas consumption.

**Target Temperature Step**

Adjusts the SAT climate entity room setpoint step.

**Maximum Relative Modulation**

Control the maximum relative modulation at which the boiler will operate.

## Terminology

**Heating Curve Coefficient**

By adjusting the heating curve coefficient, you can balance the heating loss of your home with the energy generated from your boiler at a given setpoint based on the outside temperature. Proper tuning ensures the room temperature hovers around the setpoint.

**PID Gains**

SAT offers two ways of tuning the PID gains:

- **Manual Tuning:** Fill the Proportional (`kP`), Integral (`kI`), and Derivative (`kD`) fields in the General tab with your values.
- **Automatic Gains (Recommended):** Enabled by default when the Overshoot Protection Value is present (during initial configuration). Automatic gains dynamically change the `kP`, `kI`, and `kD` values based on the heating curve value. This means that, based on the outside temperature, the gains change from mild to aggressive without intervention.

**Overshoot Protection**

This feature should be enabled when the minimum boiler capacity is greater than the control setpoint calculated by SAT. If the boiler overshoots the control setpoint, it may cycle, shortening the life of the burner. The solution is to adjust the boiler's on/off times to maintain the temperature at the setpoint while minimizing cycling.

**Overshoot Protection Value (OPV) Calculation**

The OPV is a crucial value that determines the boiler's on/off times when the Overshoot Protection feature is present (during initial configuration).

- **Automatic Calculation:** To calculate the OPV automatically, choose the **Calibrate and determine your overshoot protection value (approx. 20 min)** option during the initial configuration. SAT will then send the `MM=0` and `CS=75` commands, attempting to find the highest flow water temperature the boiler can produce while running at 0% modulation. This process takes at least 20 minutes. Once the OPV calculation is complete, SAT will resume normal operation and send a completion notification. The calculated value will be stored as an attribute in the SAT climate entity and used to determine the boiler's on/off times in the low-load control algorithm. If SAT detects that the boiler doesn't respect the 0% Max Modulation command, it will automatically change the calibration algorithm to a more sophisticated one to perform the calibration of the system.

- **Manual Calculation:** If you know the maximum flow water temperature of the boiler at 0% modulation, you can fill in this value during the initial configuration.

> **Note:** If you have any TRVs, open all of them (set them to a high setpoint) to ensure accurate calculation of the OPV. Once the calculation is complete, you can lower the setpoint back to your desired temperature.

**Automatic Duty Cycle**

When this option is enabled, SAT calculates the ON and OFF times of the boiler in 15-minute intervals, given that the kW needed to heat the home is less than the minimum boiler capacity. Additionally, using this feature, SAT can efficiently regulate the room temperature even in mild weather by automatically extending the duty cycle up to 30 minutes.

> **Tip:** For a more in-depth review of SAT and real-time observations, you can read this [excellent discussion post](https://github.com/Alexwijn/SAT/discussions/40) from [@critictidier](https://github.com/critictidier).

---

<!-- Badges -->

[hacs-url]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/hacs-default-orange.svg?style=for-the-badge
[release-badge]: https://img.shields.io/github/v/tag/Alexwijn/SAT?style=for-the-badge
[build-badge]: https://img.shields.io/github/actions/workflow/status/Alexwijn/SAT/pytest.yml?branch=develop&style=for-the-badge
[discord-badge]: https://img.shields.io/discord/1184879273991995515?label=Discord&logo=discord&logoColor=white&style=for-the-badge

<!-- References -->

[hacs]: https://hacs.xyz
[home-assistant]: https://www.home-assistant.io/
[release-url]: https://github.com/Alexwijn/SAT/releases
[discord-url]: https://discord.gg/jnVXpzqGEq
