# EHC LED Panels for Home Assistant

Control LED panels with the **LED Driver 50W-2.4G-CCT** (model **EHC_050S105DN_TP02**) and their 2.4 GHz Bluetooth LE remotes from Home Assistant.

The panels only listen: they take adverts from the remote's fixed address on Bluetooth channel 37, and never report their state. This integration sends those adverts through ESPHome boards and follows the remotes' presses to keep an assumed state.

## Needs

- One or more ESP32 boards running ESPHome with the `ble_adv_proxy` component from the `ehc-050s105dn-tp02` branch of [esphome-ble_adv_proxy](https://github.com/totalretribution/esphome-ble_adv_proxy), which adds the `adv_svc_v2` service (sending from a given address and channel). Boards with the original component can still hear remotes, but can't send.
- The boards added to Home Assistant through the ESPHome integration.

## What you get, per light

| Entity | |
| --- | --- |
| Light | On / off, with an assumed state (Home Assistant shows separate on and off buttons). On and off send the power toggle only if the assumed state differs. |
| Buttons | Power (a true toggle), Night, Max, Brighter, Dimmer, Warmer, Cooler |
| Event "Remote" | Fires on each remote press that reaches the light: `power`, `night`, `max`, `brighter`, `dimmer`, `cooler`, `warmer`, `max2`, `group1`, `group2`, `other`, with `remote_id` and `group` |

Action `ehc_led_panel.send_command`: send any command (name or number) to a light, `count` times like a held button (a press every 0.21 s).

Remote presses keep the state in step: power toggles the light, and group 1 / group 2 toggle the lights paired to that group. A press heard by several boards, or one of Home Assistant's own sends heard back, counts once.

## Adding a light

Settings → Devices & services → Add integration → **EHC LED Panels**:

1. Press any button on the light's remote near a board.
2. Choose the remote, and the group the light is paired to on it (1 for most lights).
3. The light is toggled twice to check: it should end up as it was.
4. Name it, and choose the board that sends to it.

In the light's options: change the sending board, link more remotes (e.g. a remote that controls every light), or remove them.

## Not yet

- Brightness and colour temperature as light settings: the panels only take steps, so this needs counting steps from a known state. Use the buttons for now.
- Sending from the Home Assistant host's own Bluetooth.

## Development

Tests use `pytest-homeassistant-custom-component`:

```bash
ruff format . && ruff check . && pytest
```
