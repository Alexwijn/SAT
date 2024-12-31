# Smart Autotune Thermostat

[![hacs][hacs-badge]][hacs-url]
[![release][release-badge]][release-url]
![build][build-badge]
[![discord][discord-badge]][discord-url]

**Please :star: this repo if you find it useful.**

**Your support for the countless hours we've dedicated to this development is greatly appreciated, though not required**


<a href="https://www.buymeacoffee.com/alexwijn"><img src="https://img.buymeacoffee.com/button-api/?text=Buy me a coffee&emoji=&slug=alexwijn&button_colour=0ac982&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff"></a>

![opentherm-mqtt.png](https://raw.githubusercontent.com/Alexwijn/SAT/develop/.github/images/opentherm-mqtt.png)
![overshoot_protection.png](https://raw.githubusercontent.com/Alexwijn/SAT/develop/.github/images/overshoot_protection.png)


## What is the Smart Autotune Thermostat?

The Smart Autotune Thermostat, or SAT for short, is a custom component for Home Assistant that seamlessly integrates with the following devices:
- [OpenTherm Gateway (OTGW)](https://otgw.tclcode.com/) (MQTT or Serial)
- [DIYLess](https://diyless.com/) Master OpenTherm Shield
- [Ihor Melnyk's](http://ihormelnyk.com/opentherm_adapter) OpenTherm adapter
- [Jiří Praus'](https://www.tindie.com/products/jiripraus/opentherm-gateway-arduino-shield/) OpenTherm Gateway Arduino Shield

It can also function as a PID ON/OFF thermostat, providing advanced temperature control based on Outside Temperature compensation and the Proportional-Integral-Derivative (PID) algorithm. Unlike other thermostat components, SAT supports automatic gain tuning and heating curve coefficients. This capability allows it to determine the optimal setpoint for your boiler without any manual intervention.

## Features
OpenTherm ( MQTT / Serial / ESPHome ):
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
### HACS

Smart Autotune Thermostat ( SAT ) is available in [HACS][hacs] (Home Assistant Community Store).

Use this link to directly go to the repository in HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Alexwijn&repository=SAT)

_or_

1. Install HACS if you don't have it already
2. Open HACS in Home Assistant
3. Search for "Smart Autotune Thermostat"
4. Click the download button. ⬇️

### Manual
1. Download the latest release of the SAT custom component from the GitHub repository.
2. Copy the sat directory to the custom_components directory in your Home Assistant configuration directory. If the custom_components directory doesn't exist, create it.
3. Restart Home Assistant to load the SAT custom component.
4. After installing the SAT custom component, you can configure it via the Home Assistant Config Flow interface.

# Configuration
SAT is configured using a config flow. After installation, go to the Integrations page in Home Assistant, click on the Add Integration button, and search for SAT if the autodiscovery feature fails.

## OpenTherm

1. OpenTherm Connection
   - OpenTherm Gateway MQTT:
        - Name of the thermostat
        - Top Topic ( *MQTT Top Topic* found in OTGW-firmware Settings )
        - Device

   - OpenTherm Gateway Serial:
        - Name of the thermostat
        - URL

   - ESPHome Opentherm:
        - Name of the thermostat
        - Device

> [!Important]
> The ESPHome yaml needs to follow the exact naming of the following entities, otherwise SAT will not be able to find them in Home Assistant.
<details>
<summary>ESPHome minimal yaml configuration</summary>
   
```yaml
# Insert usual esphome configuration (board, api, ota, etc.)

opentherm:
  in_pin: # insert in pin
  out_pin: # insert out pin
  ch_enable: true
  dhw_enable: true

number:
  - platform: opentherm
    t_dhw_set:
      name: t_dhw_set
      step: 1
      restore_value: true
    t_set:
      name: t_set
      restore_value: true
    max_t_set:
      name: max_t_set
      step: 1
      restore_value: true
    max_rel_mod_level:
      name: max_rel_mod_level
      min_value: 0
      max_value: 100
      step: 1
      initial_value: 100
      restore_value: true

sensor:
  - platform: opentherm
    rel_mod_level:
      name: rel_mod_level
    device_id:
      name: device_id
    t_boiler:
      name: t_boiler
    t_ret:
      name: t_ret
    max_capacity:
      name: max_capacity
    min_mod_level:
      name: min_mod_level
    t_dhw_set_lb:
      name: t_dhw_set_lb
    t_dhw_set_ub:
      name: t_dhw_set_ub

binary_sensor:
  - platform: opentherm
    flame_on:
      name: flame_on

switch:
  - platform: opentherm
    dhw_enable:
      name: dhw_enable
    ch_enable:
      name: ch_enable
```

For more information about which other entities are available for OpenTherm please visit the [ESPHome OpenTherm documentation](https://esphome.io/components/opentherm.html)

</details>

2. Configure sensors:
    - Inside Temperature sensor ( Your Room Temperature sensor )
    - Outside Temperature sensor ( Your Outside Temperature sensor )
    - Inside Humidity Sensor ( Your Room Humidity sensor )

> [!NOTE]
> For better results use an Inside Temperature sensor that reports two decimals and has a refresh rate of 30 seconds.

3. Heating System: Selecting the correct heating system type is important for SAT to accurately control the temperature and optimize performance. Choose the option that matches your setup to ensure proper temperature regulation throughout your home.

4. Areas:  
   - Primary: Users can add their physical thermostat. SAT will syncronize the `hvac_action` of the physical thermostat with the SAT climate entity's `hvac action`, that means if the physical thermostat doesn't require heating then the SAT climate entity `hvac_action` will remain at idle. Also the physical thermostat's room setpoint stays in sync with SAT climate entity. Moreover the physical thermostat will act as a back up if any failure to HA occurs.
   - Rooms: Users can add their TRV climate entities. So when any of the rooms will ask for heating, SAT will start the boiler.
> [!Note]
> If SAT is the only climate entity, skip this step.

> [!TIP]
> Look at the Heating Mode setting in General Tab for further customization.

5. Calibrate System: Optimize your heating system by automatically determining the optimal PID values for your setup. When selecting Automatic Gains, please note that the system will go through a calibration process that may take approximately 20 minutes to complete.

If you already know this value, then use the "Manually enter the overshoot protection value" option and fill the value.

Automatic Gains are recommended for most users as it simplifies the setup process and ensures optimal performance. However, if you're familiar with PID control and prefer to manually set the values, you can choose to skip Automatic Gains.

Please note that choosing to skip Automatic Gains requires a good understanding of PID control and may require additional manual adjustments to achieve optimal performance.

## PID ON/OFF

To be completed

# Configure

## General tab:
*Heating Curve Version*: Represents the 3 formulas of calculation. The available options are:

Radiators:
- [Classic Curve](https://www.desmos.com/calculator/cy8gjiciny)
- [Quantum Curve](https://www.desmos.com/calculator/hmrlrapnxz)
- [Precision Curve](https://www.desmos.com/calculator/spfvsid4ds) ( Recommented )

Underfloor:
- [Classic Curve](https://www.desmos.com/calculator/exjth5qsoe)
- [Quantum Curve](https://www.desmos.com/calculator/ke69ywalcz)
- [Precision Curve](https://www.desmos.com/calculator/i7f7uuyaoz) ( Recommented )

> [!NOTE]
> Graph parameters:
> - a: Heating Curve Value
> - b: Room Setpoint

> [!TIP]
> You can add the graph as an `iframe` card in HA.
> 
> Example:
> ```yaml
> type: iframe
> url: https://www.desmos.com/calculator/spfvsid4ds
> allow_open_top_navigation: true
> allow: fullscreen
> aspect_ratio: 130%

*PID Controller Version*: 
- Classic Controller
- Improved Controller

*Heating Mode*: 

> [!NOTE]
>Available only for multiroom installations

- Comfort ( SAT monitors the climates in other rooms to determine the error. It selects the highest error value as the PID error value for the current room )
- Eco ( SAT monitors **only** the Main thermostat's error and it is used as the PID error )

*Maximum Setpoint*:
You can choose the max water setpoint for your system.
For radiator installations, it is recommended to choose a value between 55-75 °C.
For underfloor installations, the recommended max water setpoint is 50 °C.

> [!NOTE]
>  Radiators: Higher Max water setpoint values will cause a more aggressive warm-up.

*Heating Curve Coefficient*:
The heating curve coefficient is a configurable parameter in SAT that allows you to adjust the relationship between the outdoor temperature and the heating system output. This is useful for optimizing the heating system's performance in different weather conditions, as it allows you to adjust how much heat the system delivers as the outdoor temperature changes. By tweaking this parameter, you can achieve a more efficient and comfortable heating system.

*Automatic Gains Value*: Automatically tweaking the aggressiveness of the Kp, Ki and Kd gains. 

> [!TIP]
> Best results when the user uses the same value as the Heating Curve Coefficient value.

*Derivative Time Weight*: Further tweaking of the Kd value.

> [!TIP]
> Better start with the value `2`.

*Adjustment Factor for Return Temperature*:
This factor adjusts the heating setpoint based on the boiler's return temperature, affecting heating responsiveness and efficiency. A higher value increases sensitivity to temperature changes, enhancing control over comfort and energy use. 

> [!TIP]
> Recommended starting range is 0.1 to 0.5. Adjust to suit your system and comfort preferences.

*Contact Sensor*: You can add contact sensors to avoid wasting energy when a door/window is open. When the door/window is closed again, SAT restores heating.

## Presets tab:
Predefined temperature settings for different scenarios or activities.

## Advanced Tab
*Thermal Comfort*: Uses as temperature sensor the Summer SImmer Index. The Summer Simmer Index refers to the perceived temperature based on the measured air temperature and relative humidity.

 *Dynamic Minimum Setpoint (Experimental)*: The Boiler flow water temperature may exceed the Overshoot Protection Value during Low-Load Control in multiroom installations ( Some valves may be closed ). We developed a mechanishm that monitors the boiler return water temperature and changes accordingly the Control Setpoint that is sent to the boiler. See *Adjustment Factor for Return Temperature*.

*Minimum Consumption*: The user can find this value at the boiler's manual. SAT uses this value in order to calculate the instant gas consumption.

*Maximum Consumption*: The user can find this value at the boiler's manual. SAT uses this value in order to calculate the instant gas consumption.

*Target Temperature Step*: SAT climate entity room setpoint step.

*Maximum Relative Modulation*: The user is able to control the maximum relative modulation that the boiler will operate.

# Terminology
*Heating Curve Coefficient*: By adjusting the heating curve coefficient, you can balance the heating loss of your home with the energy generated from your boiler at a given setpoint based on the outside temperature. When this value is properly tuned, the room temperature should hover around the setpoint.

*Gains*: SAT offers two ways of tuning the PID gains - manual and automatic.

- Manual tuning: You can fill the Proportional, Integral, and Derivative fields in the General tab with your values.
- Automatic Gains ( Recommended ): This option is enabled by default when the Overshoot protection value is present (During initial configuration). Automatic gains dynamically change the kP, kI, and kD values based on the heating curve value. So, based on the outside temperature, the gains change from mild to aggressive without intervention.

*Overshoot Protection*: This feature should be enabled when the minimum boiler capacity is greater than the control setpoint calculated by SAT. If the boiler overshoots the control setpoint, it may cycle, shortening the life of the burner. The solution is to adjust the boiler's on/off times to maintain the temperature at the setpoint while minimizing cycling.

*Overshoot Protection Value (OPV) Calculation*:
The OPV is a crucial value that determines the boiler's on/off times when the Overshoot Protection feature is present (During initial configuration).

*Automatic Calculation*: To calculate the OPV automatically, choose the "Calibrate and determine your overshoot protection value (approx. 20 min)" option during the initial configuration. SAT will then send the MM=0 and CS=75 commands, attempting to find the highest flow water temperature the boiler can produce while running at 0% modulation. This process takes at least 20 minutes. Once the OPV calculation is complete, SAT will resume normal operation and send a completion notification. The calculated value will be stored as an attribute in the SAT climate entity and used to determine the boiler's on/off times in the low-load control algorithm. If SAT detects that the boiler doesn't respect the 0% Max Modulation command, it will automatically change the calibration algorithm to a more sophisticated one to perform the calibration of the system.

*Manual Calculation*: If you know the maximum flow water temperature of the boiler at 0% modulation, you can fill in this value during the initial configuration.

> [!Note]
> If you have any TRVs, open all of them (set them to a high setpoint) to ensure accurate calculation of the OPV. Once the calculation is complete, you can lower the setpoint back to your desired temperature.

*Automatic Duty Cycle*: When this option is enabled, SAT calculates the ON and OFF times of the boiler in 15-minute intervals, given that the kW needed to heat the home is less than the minimum boiler capacity. Moreover, using this feature, SAT can efficiently regulate the room temperature even in mild weather by automatically extending the duty cycle up to 30 minutes.

> [!TIP]
> For more in depth review of SAT and real time observations you can read this [excellent discussion post](https://github.com/Alexwijn/SAT/discussions/40) from @critictidier.

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
