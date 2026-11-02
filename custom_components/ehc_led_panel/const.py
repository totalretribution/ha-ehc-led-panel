"""Constants."""

DOMAIN = "ehc_led_panel"

CONF_LINKS = "links"
CONF_REMOTE_ID = "remote_id"
CONF_GROUP = "group"
CONF_PROXY = "proxy"

# ESPHome boards running ble_adv_proxy (esphome-ble_adv_proxy) with adv_svc_v2
ESPHOME_DOMAIN = "esphome"
RAW_ADV_EVENT = "esphome.ble_adv.raw_adv"
PROXY_NAME_SENSOR_SUFFIX = "_ble_adv_proxy_name"
SETUP_SVC = "setup_svc_v0"
ADV_SVC = "adv_svc_v2"
PROXY_IGNORED_DURATION_MS = 2000  # the board forwards an identical advert once in this time

DEDUP_SECONDS = 2.0  # a press heard again within this time (other board, our own send) is ignored
SEND_GAP_SECONDS = 0.1  # between two sends of the same light, after the advert ends
