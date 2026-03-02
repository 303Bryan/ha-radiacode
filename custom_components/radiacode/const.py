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
