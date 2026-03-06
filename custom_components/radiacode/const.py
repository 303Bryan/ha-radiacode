"""Constants for the RadiaCode integration."""

DOMAIN = "radiacode"

# Config-entry data keys
CONF_ADDRESS = "address"
CONF_NAME = "name"

# Sensor keys — must match RadiaCodeData field names exactly
SENSOR_DOSE_RATE        = "dose_rate"
SENSOR_COUNT_RATE       = "count_rate"
SENSOR_ACCUMULATED_DOSE = "accumulated_dose"
SENSOR_BATTERY          = "battery"
SENSOR_TEMPERATURE      = "temperature"

# Switch keys — must match RadiaCodeSettings field names exactly
SWITCH_SOUND_ON     = "sound_on"
SWITCH_VIBRO_ON     = "vibro_on"
SWITCH_DISPLAY_ON   = "display_on"
SWITCH_BACKLIGHT_ON = "display_backlight_on"

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
