# Danfoss Ally Gateway

[![HACS][hacs-badge]][hacs-url]
[![GitHub Release][release-badge]][release-url]
[![License][license-badge]][license-url]

A [Home Assistant](https://www.home-assistant.io/) custom integration that replaces the Danfoss Ally Gateway hardware by replicating its Zigbee coordination features locally. Pair your Danfoss Ally TRVs directly with [Zigbee2MQTT](https://www.zigbee2mqtt.io/) (ZHA support planned) and let this integration handle room management, scheduling, load balancing, and more.

## Features

- **Room-based TRV management** -- group multiple TRVs into rooms with a virtual thermostat
- **External temperature forwarding** -- use any HA temperature sensor instead of the TRV's built-in sensor, with Danfoss-compliant timing for covered and exposed radiator modes
- **Weekly schedule programming** -- program up to 6 daily transitions per day via ZCL thermostat commands
- **Preheat** -- start heating before a scheduled temperature increase to reach the target on time
- **Multi-TRV load balancing** -- balance heating load across TRVs in a room
- **Window open detection** -- detect and propagate window-open state across TRVs in a room
- **Remote climate sync** -- bidirectional setpoint synchronization with another HA climate entity
- **Heat source monitoring** -- track heating system availability via a climate or binary sensor entity
- **Time synchronization** -- periodic time sync to TRVs
- **Power-cycle detection** -- automatic schedule re-programming after TRV power loss

## Supported Devices

- Danfoss Ally TRVs
- Popp TRVs (Danfoss Ally compatible)
- Hive TRVs (Danfoss Ally compatible)

## Requirements

- Home Assistant 2025.6.0 or newer
- [Zigbee2MQTT](https://www.zigbee2mqtt.io/) with MQTT integration configured in HA
- TRVs paired to your Zigbee2MQTT network

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots in the top right corner and select **Custom repositories**
3. Add this repository URL with category **Integration**
4. Search for "Danfoss Ally Gateway" and download it
5. Restart Home Assistant

### Manual

1. Download the latest release from the [releases page][release-url]
2. Copy the `custom_components/danfoss_ally_gateway` directory to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

## Configuration

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for **Danfoss Ally Gateway**
3. Select your Zigbee backend (Zigbee2MQTT) and configure the MQTT base topic (default: `zigbee2mqtt`)
4. Add rooms as subentries, each with:
   - **Room name** and area assignment
   - **TRV devices** -- select one or more Danfoss Ally TRVs
   - **External temperature sensor** (optional) -- any HA temperature sensor
   - **Heat source** (optional) -- a climate or binary sensor entity
   - **Remote climate** (optional) -- a climate entity for bidirectional setpoint sync
   - **Schedule helper** (optional) -- a schedule helper entity defining at-home/away periods
   - **At-home / away temperatures** -- target temperatures for scheduled periods
   - **Preheat** -- enable to pre-heat before scheduled temperature increases

## Entities

Each room creates the following entities:

| Platform | Entity | Description |
|----------|--------|-------------|
| Climate | Room thermostat | Virtual thermostat representing the room |
| Binary Sensor | Heat Required | Whether any TRV in the room is calling for heat |
| Binary Sensor | Heat Available | Whether the heat source is active |
| Binary Sensor | Window Open | Whether a window-open condition is detected |
| Sensor | Heating Demand | Per-TRV heating demand percentage |
| Sensor | Load Estimate | Per-TRV load estimate |
| Sensor | Load Room Mean | Average load estimate across all TRVs in the room |
| Select | Programming Mode | Manual, Schedule, Schedule + Preheat, or Pause |
| Switch | Load Balancing | Enable/disable multi-TRV load balancing |

## Services

| Service | Description |
|---------|-------------|
| `danfoss_ally_gateway.set_room_schedule` | Program a weekly heating schedule to all TRVs in a room |
| `danfoss_ally_gateway.clear_room_schedule` | Clear the schedule and set TRVs to manual mode |
| `danfoss_ally_gateway.set_schedule_mode` | Set programming mode (manual/schedule/preheat/eco) |

## Contributing

Contributions are welcome. Please open an issue or pull request on the [GitHub repository](https://github.com/storm1ng/hass-danfoss-ally-gateway).

## License

This project is licensed under the MIT License -- see the [LICENSE](LICENSE) file for details.

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://hacs.xyz/
[release-badge]: https://img.shields.io/github/v/release/storm1ng/hass-danfoss-ally-gateway
[release-url]: https://github.com/storm1ng/hass-danfoss-ally-gateway/releases
[license-badge]: https://img.shields.io/github/license/storm1ng/hass-danfoss-ally-gateway
[license-url]: https://github.com/storm1ng/hass-danfoss-ally-gateway/blob/main/LICENSE
