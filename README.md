# PHD2 MQTT Bridge

This tool connects to the **PHD2 guiding server API** and publishes **real-time guiding data** to an MQTT broker.
It exposes each guiding metric as a Home Assistant-compatible MQTT sensor using **MQTT Discovery**, and includes a **guide star availability** binary sensor plus an **availability (LWT)** topic so Home Assistant can track whether the bridge is online.

This is useful for:

- Real-time dashboards
- Triggering automations based on guider performance
- Logging or long-term trending
- Observatory monitoring and alerts

The bridge can run directly on your observatory computer or inside a lightweight Docker container.

---

## Features

- Connects to the PHD2 JSON-RPC server (default port 4400).
- Publishes live guiding metrics:
  - RA error (arcsec)
  - Dec error (arcsec)
  - Total error (arcsec)
  - dx, dy offsets (pixels)
  - SNR
  - AvgDist
- Emits a **binary sensor**: `guide_star_available`
  - `ON` when PHD2 is receiving valid GuideStep events
  - `OFF` if a StarLost event arrives
- Home Assistant MQTT Discovery support
  - Sensors appear automatically in HA
  - Includes a device grouping based on environment variables
- MQTT LWT for sensor availability (`online` / `offline`)
- Detailed debug logging for troubleshooting (`LOG_LEVEL=DEBUG`)
- Configurable entirely via environment variables
- Supports running multiple independent bridge instances for multiple mounts

---

## Requirements

- PHD2 with **Tools → Enable Server** enabled
- Python 3.12 or Docker
- MQTT broker (Mosquitto, EMQX, HiveMQ, etc.)
- Optional: Home Assistant MQTT integration

---

# Running Directly (Without Docker)

Install dependency:

```bash
pip install paho-mqtt
```

Run with default settings:
```bash
python phd2_mqtt_bridge.py
```

To use environment variables:
```bash
PHD2_HOST=192.168.1.50 \
MQTT_HOST=192.168.1.10 \
BASE_TOPIC="phd2/guiding_rig1" \
DEVICE_ID="phd2_rig1" \
LOG_LEVEL=DEBUG \
python phd2_mqtt_bridge.py
```

---

# Docker Usage

## Dockerfile

Build the image:

```bash
docker build -t phd2-mqtt-bridge .
```

Run it:

```bash
docker run --rm \
  -e PHD2_HOST=phd2-host \
  -e MQTT_HOST=mosquitto \
  -e BASE_TOPIC="phd2/guiding" \
  -e DEVICE_ID="phd2_guiding_server" \
  -e LOG_LEVEL=DEBUG \
  phd2-mqtt-bridge
```

## Docker Compose example

Bring it up:

```bash
docker compose up -d
```

Check logs:

```bash
docker compose logs -f phd2-mqtt-bridge
```

## Environment Variables

Variable           |    Default        |  Description
-------------------|-------------------|----------------------
PHD2_HOST          |   127.0.0.1       |  Host running PHD2
PHD2_PORT          |   4400            |  PHD2 server port
MQTT_HOST          |   127.0.0.1       |  MQTT broker host
MQTT_PORT          |   1883            |  MQTT broker port
MQTT_USERNAME      |   (none)          |  MQTT username
MQTT_PASSWORD      |   (none)          |  MQTT password
MQTT_CLIENT_ID     |   phd2_bridge     |  MQTT client name
MQTT_KEEPALIVE     |   60              |  Keepalive in seconds
DISCOVERY_PREFIX   |   homeassistant   |  HA discovery root
BASE_TOPIC         |   phd2/guiding    |  Base topic for all sensor data
DEVICE_ID          |   phd2_guiding_server | Unique device ID for Home Assistant
DEVICE_NAME        |   PHD2 Guiding    |  Display name
DEVICE_MANUFACTURER|   Open PHD Guiding|  Display manufacturer
DEVICE_MODEL       |   PHD2 Server     |  Display model
LOG_LEVEL          |   INFO            |  Set to DEBUG for raw PHD2 event logging

---

# What You Should See in Home Assistant

After the container starts and publishes discovery messages, Home Assistant should automatically detect a device:

**Device:**
PHD2 Guiding (Rig 1)

Sensors:
* PHD2 RA Error
* PHD2 Dec Error
* PHD2 Total Error
* PHD2 dx
* PHD2 dy
* PHD2 SNR
* PHD2 Avg Dist

Binary sensor:
* PHD2 Guide Star Available

Device availability:
* Controlled via MQTT LWT on BASE_TOPIC/availability

---

# Troubleshooting

**No guiding data appears**

Enable debug logging:

```bash
LOG_LEVEL=DEBUG docker compose up
```

You should see raw PHD2 messages like:

```
DEBUG Raw line from PHD2: b'{"Event":"GuideStep", ... }'
INFO GuideStep at 2025-01-01 00:00:00 RA_raw=0.41 Dec_raw=-0.22 Total=0.46 ...
```

If no such messages appear:
1. Verify Tools → Enable Server is enabled in PHD2
2. Check hostnames and networks inside Docker
3. Confirm that PHD2 is guiding; GuideStep arrives once per exposure

MQTT sensors appear but never update
• Check BASE_TOPIC is unique per instance
• Verify MQTT authentication
• Confirm Home Assistant MQTT integration is active
