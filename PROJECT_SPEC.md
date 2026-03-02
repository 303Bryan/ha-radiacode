# RadiaCode Home Assistant Integration — Project Specification

## Project Overview

Build a **custom Home Assistant integration** (`ha-radiacode`) that connects to RadiaCode radiation detector devices (RC-102, RC-103, RC-103G, RC-110) via **Bluetooth Low Energy (BLE)** using Home Assistant's native Bluetooth framework. This integration must support **ESPHome Bluetooth proxies** for range extension.

### Target Sensors
- **Dose rate** (µSv/h) — real-time ambient radiation dose rate
- **Accumulated dose** (µSv) — total accumulated radiation dose
- **Count rate** (CPS) — counts per second from the detector
- **Battery level** (%) — device battery percentage

### Key Design Decisions
- Use **`bleak`** (not `bluepy`) for BLE communication — this is required for HA Bluetooth proxy support
- Build a standalone **`radiacode-ble`** Python library that wraps the BLE protocol using `bleak`, then use it in the HA integration
- Model after modern HA BLE integrations (like `led_ble`, `sensorpush_ble`, `inkbird_ble`)
- This is a **custom component** (installed via HACS), not a core integration

---

## Architecture

```
ha-radiacode/
├── custom_components/
│   └── radiacode/
│       ├── __init__.py          # Integration setup, device coordinator
│       ├── manifest.json        # Integration metadata
│       ├── config_flow.py       # UI-based configuration (Bluetooth discovery)
│       ├── const.py             # Constants (domain, sensor keys, etc.)
│       ├── sensor.py            # Sensor platform (dose_rate, accumulated_dose, etc.)
│       ├── coordinator.py       # DataUpdateCoordinator for polling device
│       ├── radiacode_ble/       # Embedded BLE client library
│       │   ├── __init__.py
│       │   ├── client.py        # BleakClient-based RadiaCode BLE client
│       │   └── protocol.py      # Command encoding/decoding (from cdump/radiacode)
│       └── strings.json         # UI strings
├── hacs.json                    # HACS metadata
├── README.md
└── LICENSE
```

### Why embed the BLE library?

The upstream `cdump/radiacode` library uses `bluepy` for Bluetooth, which is Linux-only and incompatible with HA's `bleak`-based Bluetooth proxy system. We need to reimplement the BLE transport layer using `bleak` while reusing the protocol/command layer from `cdump/radiacode`. The protocol logic (command encoding, data parsing) can be adapted from the upstream library which is MIT-licensed.

---

## Phase 1: BLE Protocol Library (`radiacode_ble/`)

### Reference: cdump/radiacode Protocol

The RadiaCode uses a proprietary BLE protocol. The device advertises with a local name starting with `"RC-"` (e.g., `"RC-103"`, `"RC-102"`). Key facts:

- **No pairing required** — the device uses open BLE connections
- **Communication model**: Request/response over BLE GATT characteristics
- The device exposes a custom BLE service with two characteristics:
  - One for **writing commands** (Write Without Response)
  - One for **reading responses** (Notify)
- **Device name prefix**: `"RC-"` — used for discovery matching in manifest.json

### BLE Protocol Details (from cdump/radiacode source)

The protocol uses a simple command/response pattern:

1. **Send a command**: Write a binary command packet to the write characteristic
2. **Receive response**: Read the response from the notify characteristic
3. Commands are structured as: `[sequence_byte, command_id_low, command_id_high, ...data]`

Key commands we need:

| Command | ID | Description |
|---------|-----|-------------|
| `VS_DATA_BUF` | `0x0A06` | Get buffered real-time data (dose_rate, count_rate, dose) |
| `VS_SERIAL_NUMBER` | `0x0108` | Get device serial number |
| `VS_FW_SIGNATURE` | `0x0408` | Get firmware version/signature |
| `VS_BATTERY` | Various | Get battery level (parsed from data_buf) |

### Implementation Approach

Study the source code at https://github.com/cdump/radiacode carefully, specifically:
- `src/radiacode/transports/bluetooth.py` — BLE transport using bluepy (we rewrite this with bleak)
- `src/radiacode/radiacode.py` — Main device class with command methods
- `src/radiacode/bytes_buffer.py` — Binary data parsing helpers
- `src/radiacode/types.py` — Data types (RealTimeData, Spectrum, etc.)
- `src/radiacode/decoders/databuf.py` — Decoder for the data buffer response

The `client.py` in our library should:

```python
# radiacode_ble/client.py — Conceptual structure
import asyncio
from bleak import BleakClient
from bleak_retry_connector import establish_connection

class RadiaCodeBLEClient:
    """BLE client for RadiaCode devices using bleak."""

    def __init__(self):
        self._client: BleakClient | None = None
        self._notify_data: bytearray = bytearray()
        self._notify_event: asyncio.Event = asyncio.Event()

    async def connect(self, ble_device, **kwargs):
        """Connect to RadiaCode device."""
        self._client = await establish_connection(
            BleakClient, ble_device, ble_device.address,
            timeout=10.0,
        )
        # Discover services, find the write and notify characteristics
        # Start notification handler

    async def disconnect(self):
        """Disconnect from device."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()

    async def _send_command(self, command_id: int, data: bytes = b"") -> bytes:
        """Send command and wait for response."""
        # Build command packet
        # Write to write characteristic
        # Wait for notify response
        # Return response data

    async def get_data(self) -> dict:
        """Get real-time radiation data."""
        # Send VS_DATA_BUF command
        # Parse response into dict with dose_rate, count_rate, dose, etc.

    async def get_serial_number(self) -> str:
        """Get device serial number."""

    async def get_firmware_version(self) -> str:
        """Get firmware version."""
```

### Important: BLE Characteristic Discovery

RadiaCode devices use a custom BLE service. The UUIDs need to be discovered from the device at connection time. Look at how the `cdump/radiacode` bluetooth transport discovers services and identifies the correct write/notify characteristics. The Arduino library (`mkgeiger/RadiaCode`) may also have documented UUIDs.

---

## Phase 2: Home Assistant Integration

### manifest.json

```json
{
  "domain": "radiacode",
  "name": "RadiaCode",
  "version": "0.1.0",
  "codeowners": ["@303Bryan"],
  "config_flow": true,
  "dependencies": ["bluetooth_adapters"],
  "documentation": "https://github.com/303Bryan/ha-radiacode",
  "integration_type": "device",
  "iot_class": "local_polling",
  "bluetooth": [
    {
      "local_name": "RC-*"
    }
  ],
  "requirements": ["bleak-retry-connector>=3.0.0"]
}
```

Key points:
- `"bluetooth"` matcher uses `"local_name": "RC-*"` to auto-discover RadiaCode devices
- `"dependencies": ["bluetooth_adapters"]` ensures BT proxy support
- `"iot_class": "local_polling"` since we poll the device periodically
- `"config_flow": true` for UI-based setup

### Config Flow (`config_flow.py`)

The config flow should:
1. **Discovery step**: HA's Bluetooth integration detects a device with name `RC-*` and triggers our config flow
2. **User confirmation**: Show the discovered device name and MAC address, ask user to confirm
3. **Manual entry**: Allow user to manually enter a Bluetooth MAC address if discovery doesn't work
4. **Connection test**: Briefly connect to verify the device responds, get serial number

```python
# Conceptual config flow
class RadiaCodeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak):
        """Handle Bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(self, user_input=None):
        """Confirm Bluetooth discovery."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovery_info.name,
                data={
                    "address": self._discovery_info.address,
                    "name": self._discovery_info.name,
                },
            )
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._discovery_info.name},
        )

    async def async_step_user(self, user_input=None):
        """Handle manual configuration."""
        # Show form for manual MAC address entry
```

### Data Coordinator (`coordinator.py`)

Uses HA's `DataUpdateCoordinator` pattern to poll the device periodically:

```python
class RadiaCodeCoordinator(DataUpdateCoordinator):
    """Coordinator for polling RadiaCode device."""

    def __init__(self, hass, entry):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )
        self._address = entry.data["address"]
        self._client = RadiaCodeBLEClient()

    async def _async_update_data(self):
        """Fetch data from RadiaCode device."""
        # Get BLE device from HA's Bluetooth manager
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
        if not ble_device:
            raise UpdateFailed("Device not found")

        try:
            await self._client.connect(ble_device)
            data = await self._client.get_data()
            return data
        except Exception as err:
            raise UpdateFailed(f"Error communicating: {err}") from err
        finally:
            await self._client.disconnect()
```

**Important**: Don't hold the BLE connection open between polls. Connect, read data, disconnect. This is friendlier to BLE proxy connection slots.

### Sensor Platform (`sensor.py`)

Define sensor entities:

```python
SENSOR_DESCRIPTIONS = [
    SensorEntityDescription(
        key="dose_rate",
        name="Dose Rate",
        native_unit_of_measurement="µSv/h",
        device_class=None,  # No standard device class for radiation
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:radioactive",
    ),
    SensorEntityDescription(
        key="accumulated_dose",
        name="Accumulated Dose",
        native_unit_of_measurement="µSv",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:radioactive",
    ),
    SensorEntityDescription(
        key="count_rate",
        name="Count Rate",
        native_unit_of_measurement="CPS",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:pulse",
    ),
    SensorEntityDescription(
        key="battery",
        name="Battery",
        native_unit_of_measurement="%",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]
```

### strings.json

```json
{
  "config": {
    "step": {
      "bluetooth_confirm": {
        "title": "RadiaCode Device Found",
        "description": "Found RadiaCode device: {name}. Do you want to add it?"
      },
      "user": {
        "title": "RadiaCode",
        "description": "Enter the Bluetooth MAC address of your RadiaCode device.",
        "data": {
          "address": "Bluetooth Address"
        }
      }
    },
    "abort": {
      "already_configured": "Device is already configured."
    }
  }
}
```

---

## Phase 3: HACS Distribution

### hacs.json

```json
{
  "name": "RadiaCode",
  "render_readme": true,
  "homeassistant": "2024.1.0"
}
```

### README.md should include:
- What the integration does
- Supported devices (RC-102, RC-103, RC-103G, RC-110)
- Prerequisites (Bluetooth adapter or ESPHome BT proxy)
- Installation via HACS
- Screenshots of sensors
- Troubleshooting section

---

## Key Technical References

### Must-read source code (clone and study these):
1. **https://github.com/cdump/radiacode** — The canonical Python library for RadiaCode. Study the BLE protocol implementation, command encoding/decoding, and data parsing. MIT licensed.
2. **https://github.com/mkgeiger/RadiaCode** — Arduino/ESP32 C++ library. Has documented BLE UUIDs and protocol details since the author reverse-engineered from the Python library.
3. **https://github.com/vooon/hass-radiacode** — Archived HA integration attempt (deprecated). Shows basic HA integration structure but uses the old bluepy transport.
4. **https://github.com/haberda/radiacode2mqtt** — MQTT bridge approach. Shows working data parsing but different architecture.

### HA Developer Docs:
- Bluetooth integration: https://developers.home-assistant.io/docs/bluetooth/
- Bluetooth APIs: https://developers.home-assistant.io/docs/core/bluetooth/api/
- Integration manifest: https://developers.home-assistant.io/docs/creating_integration_manifest/
- Config flow: https://developers.home-assistant.io/docs/config_entries_config_flow_handler/

### Key Libraries:
- `bleak` — Cross-platform BLE library (HA standard)
- `bleak-retry-connector` — Reliable BLE connections with retry logic
- `bluetooth-data-tools` — HA Bluetooth utilities

---

## Development Workflow

### Step-by-step implementation order:

1. **Clone and study `cdump/radiacode`** source to understand the BLE protocol
   - Identify the BLE service UUID and characteristic UUIDs
   - Map out the command/response protocol
   - Understand the data_buf response format

2. **Build `radiacode_ble/protocol.py`** — Pure protocol logic (no BLE)
   - Command packet builder
   - Response parser (for data_buf, serial number, etc.)
   - Data types

3. **Build `radiacode_ble/client.py`** — BLE client using bleak
   - Connect/disconnect
   - Service/characteristic discovery
   - Command send/receive with notifications
   - High-level methods (get_data, get_serial, etc.)

4. **Build the HA integration scaffold**
   - manifest.json, const.py, __init__.py
   - config_flow.py with Bluetooth discovery
   - strings.json

5. **Build coordinator.py** — Data polling coordinator

6. **Build sensor.py** — Sensor entities

7. **Test on real hardware** (RC-103 + BT proxy)

8. **Polish** — Error handling, reconnection logic, README

---

## Testing Notes

- The developer has a **RadiaCode-103** device for testing
- Home Assistant is running on **HAOS**
- **ESPHome Bluetooth proxies** are available for BLE range extension
- All BLE communication should use `bleak` through HA's Bluetooth framework to ensure proxy compatibility

---

## Important Constraints

1. **Never use `bluepy`** — it's Linux-only and incompatible with HA BT proxies
2. **Don't hold BLE connections open** — connect, read, disconnect per poll cycle
3. **Use `bleak-retry-connector`** for reliable connections
4. **Follow HA conventions** — DataUpdateCoordinator, config flow, proper device registry
5. **The `radiacode` PyPI package cannot be used directly** because it depends on `bluepy` for BT. We must reimplement the BLE transport layer using `bleak`.
