"""Constants for the RadiaCode integration."""

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo

DOMAIN = "radiacode"

# Config-entry data keys
CONF_ADDRESS = "address"
CONF_NAME = "name"

# Config-entry option keys
CONF_POLL_INTERVAL = "poll_interval"
DEFAULT_POLL_INTERVAL = 5    # seconds
MIN_POLL_INTERVAL = 5
MAX_POLL_INTERVAL = 300

# Sensor keys — must match RadiaCodeData field names exactly
SENSOR_DOSE_RATE        = "dose_rate"
SENSOR_COUNT_RATE       = "count_rate"
SENSOR_ACCUMULATED_DOSE = "accumulated_dose"
SENSOR_BATTERY          = "battery"
SENSOR_TEMPERATURE      = "temperature"
SENSOR_HARDNESS         = "hardness"

# Switch keys — must match RadiaCodeSettings field names exactly
SWITCH_SOUND_ON     = "sound_on"
SWITCH_VIBRO_ON     = "vibro_on"
SWITCH_DISPLAY_ON   = "display_on"
SWITCH_BACKLIGHT_ON = "display_backlight_on"

# Connection switch key (not a device setting — controls integration BLE state)
SWITCH_BLE_CONNECTED = "ble_connected"

# Number keys — must match RadiaCodeSettings field names exactly
NUMBER_DISPLAY_BRIGHTNESS = "display_brightness"
NUMBER_DR_ALARM_L1        = "dr_alarm_level1"
NUMBER_DR_ALARM_L2        = "dr_alarm_level2"
NUMBER_DS_ALARM_L1        = "ds_alarm_level1"
NUMBER_DS_ALARM_L2        = "ds_alarm_level2"
NUMBER_CR_ALARM_L1        = "cr_alarm_level1"
NUMBER_CR_ALARM_L2        = "cr_alarm_level2"

# Select keys — must match RadiaCodeSettings field names exactly
SELECT_DISPLAY_DIRECTION = "display_direction"
SELECT_DISPLAY_OFF_TIME  = "display_off_time"

# Button keys
BUTTON_DOSE_RESET = "dose_reset"

# Diagnostic sensor keys
SENSOR_RSSI = "rssi"

# Device-health sensor keys — must match RadiaCodeDiagnostics field names
SENSOR_SIPM_BIAS   = "sipm_bias_mv"
SENSOR_MCU_TEMP    = "mcu_temperature"
SENSOR_MCU_VREF    = "mcu_vref_mv"
SENSOR_ACC_X       = "acc_x"
SENSOR_ACC_Y       = "acc_y"
SENSOR_ACC_Z       = "acc_z"

# Radiation alarm enum sensor (moved from binary_sensor in 1.1.0)
SENSOR_RADIATION_ALARM = "radiation_alarm"
ALARM_STATE_NONE = "No Alarm"
ALARM_STATE_L1 = "L1 Alarm"
ALARM_STATE_L2 = "L2 Alarm"

# Binary sensor keys
BINARY_SENSOR_CONNECTIVITY = "connectivity"


def build_device_info(address: str, name: str) -> DeviceInfo:
    """Build a DeviceInfo dict shared by every entity on this device.

    Centralised here so that manufacturer, model, and connection data are
    defined in exactly one place.  The serial number and firmware version
    are populated later by the coordinator once the BLE link is live.
    """
    model = name if name.startswith("RC-") else "RadiaCode"
    return DeviceInfo(
        identifiers={(DOMAIN, address)},
        connections={(CONNECTION_BLUETOOTH, address)},
        name=name,
        manufacturer="303Bryan",
        model=model,
    )
