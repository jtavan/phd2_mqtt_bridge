#!/usr/bin/env python3
import socket
import json
import math
from datetime import datetime
import time
import os
import logging

import paho.mqtt.client as mqtt

# ----------------------------
# Logging
# ----------------------------

def _init_logging():
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return logging.getLogger("phd2_mqtt_bridge")

logger = _init_logging()

# ----------------------------
# Environment-driven config
# ----------------------------

def getenv_int(name, default):
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        logger.warning("Invalid int for %s=%r, using default %r", name, val, default)
        return default


# PHD2 connection
PHD2_HOST = os.environ.get("PHD2_HOST", "127.0.0.1")
PHD2_PORT = getenv_int("PHD2_PORT", 4400)

# MQTT connection
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = getenv_int("MQTT_PORT", 1883)
MQTT_USERNAME = os.environ.get("MQTT_USERNAME") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None
MQTT_CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", "phd2_guiding_bridge")
MQTT_KEEPALIVE = getenv_int("MQTT_KEEPALIVE", 60)

# MQTT topics
DISCOVERY_PREFIX = os.environ.get("DISCOVERY_PREFIX", "homeassistant")
BASE_TOPIC = os.environ.get("BASE_TOPIC", "phd2/guiding")
AVAILABILITY_TOPIC = f"{BASE_TOPIC}/availability"

# Device info for Home Assistant
DEVICE_ID = os.environ.get("DEVICE_ID", "phd2_guiding_server")
DEVICE_NAME = os.environ.get("DEVICE_NAME", "PHD2 Guiding")
DEVICE_MANUFACTURER = os.environ.get("DEVICE_MANUFACTURER", "Open PHD Guiding")
DEVICE_MODEL = os.environ.get("DEVICE_MODEL", "PHD2 Server")

DEVICE_INFO = {
    "identifiers": [DEVICE_ID],
    "name": DEVICE_NAME,
    "manufacturer": DEVICE_MANUFACTURER,
    "model": DEVICE_MODEL,
}

logger.info("Starting PHD2 MQTT bridge")
logger.info("PHD2_HOST=%s PHD2_PORT=%s", PHD2_HOST, PHD2_PORT)
logger.info("MQTT_HOST=%s MQTT_PORT=%s CLIENT_ID=%s BASE_TOPIC=%s DEVICE_ID=%s",
            MQTT_HOST, MQTT_PORT, MQTT_CLIENT_ID, BASE_TOPIC, DEVICE_ID)

# Global state
mqtt_client = None
discovery_published = False
guide_star_available = None  # None until first state, then True/False
PIXEL_SCALE_ARCSEC_PER_PX = None  # Set by get_pixel_scale


# ----------------------------
# MQTT helpers
# ----------------------------

def publish_discovery():
    """
    Publish Home Assistant MQTT discovery messages for all entities.
    Retained so HA can see them even after restarts.
    """
    global discovery_published
    if discovery_published:
        return

    logger.info("Publishing Home Assistant discovery messages")

    sensors = [
        {
            "object_id": "ra_error_arcsec",
            "name": "PHD2 RA Error",
            "unit": "arcsec",
            "icon": "mdi:axis-arrow",
            "state_topic": f"{BASE_TOPIC}/ra_error_arcsec",
            "device_class": None,
        },
        {
            "object_id": "dec_error_arcsec",
            "name": "PHD2 Dec Error",
            "unit": "arcsec",
            "icon": "mdi:axis-arrow",
            "state_topic": f"{BASE_TOPIC}/dec_error_arcsec",
            "device_class": None,
        },
        {
            "object_id": "total_error_arcsec",
            "name": "PHD2 Total Error",
            "unit": "arcsec",
            "icon": "mdi:crosshairs-gps",
            "state_topic": f"{BASE_TOPIC}/total_error_arcsec",
            "device_class": None,
        },
        {
            "object_id": "dx_px",
            "name": "PHD2 dx",
            "unit": "px",
            "icon": "mdi:axis-arrow",
            "state_topic": f"{BASE_TOPIC}/dx_px",
            "device_class": None,
        },
        {
            "object_id": "dy_px",
            "name": "PHD2 dy",
            "unit": "px",
            "icon": "mdi:axis-arrow",
            "state_topic": f"{BASE_TOPIC}/dy_px",
            "device_class": None,
        },
        {
            "object_id": "snr",
            "name": "PHD2 SNR",
            "unit": None,
            "icon": "mdi:signal",
            "state_topic": f"{BASE_TOPIC}/snr",
            "device_class": None,
        },
        {
            "object_id": "avg_dist",
            "name": "PHD2 Avg Dist",
            "unit": "arcsec",
            "icon": "mdi:chart-bell-curve",
            "state_topic": f"{BASE_TOPIC}/avg_dist",
            "device_class": None,
        },
    ]

    # Numeric sensors
    for s in sensors:
        obj_id = s["object_id"]
        unique_id = f"{DEVICE_ID}_{obj_id}"
        topic = f"{DISCOVERY_PREFIX}/sensor/{DEVICE_ID}/{obj_id}/config"

        config = {
            "name": s["name"],
            "state_topic": s["state_topic"],
            "unique_id": unique_id,
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": DEVICE_INFO,
            "state_class": "measurement",
        }

        if s["unit"] is not None:
            config["unit_of_measurement"] = s["unit"]
        if s["device_class"] is not None:
            config["device_class"] = s["device_class"]
        if s["icon"] is not None:
            config["icon"] = s["icon"]

        logger.debug("Discovery sensor config topic=%s payload=%s", topic, config)
        mqtt_client.publish(topic, json.dumps(config), qos=1, retain=True)

    # Binary sensor for guide star availability
    obj_id = "guide_star_available"
    unique_id = f"{DEVICE_ID}_{obj_id}"
    topic = f"{DISCOVERY_PREFIX}/binary_sensor/{DEVICE_ID}/{obj_id}/config"

    config = {
        "name": "PHD2 Guide Star Available",
        "state_topic": f"{BASE_TOPIC}/guide_star_available",
        "unique_id": unique_id,
        "availability_topic": AVAILABILITY_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",
        "payload_on": "ON",
        "payload_off": "OFF",
        "device_class": "connectivity",
        "icon": "mdi:star",
        "device": DEVICE_INFO,
    }

    logger.debug("Discovery binary_sensor config topic=%s payload=%s", topic, config)
    mqtt_client.publish(topic, json.dumps(config), qos=1, retain=True)

    discovery_published = True
    logger.info("Home Assistant discovery messages published")


def set_availability(online: bool):
    """
    Publish availability state. LWT will set this to offline if
    the MQTT client disconnects ungracefully.
    """
    payload = "online" if online else "offline"
    logger.info("Publishing availability %s to %s", payload, AVAILABILITY_TOPIC)
    mqtt_client.publish(AVAILABILITY_TOPIC, payload, qos=1, retain=True)


def publish_numeric(topic: str, value):
    if value is None:
        logger.debug("Skipping publish for %s because value is None", topic)
        return
    logger.debug("Publishing numeric %s = %s", topic, value)
    mqtt_client.publish(topic, f"{value}", qos=0, retain=True)


def publish_guide_star_available(available: bool):
    global guide_star_available
    if guide_star_available == available:
        logger.debug("guide_star_available already %s, not publishing", available)
        return
    guide_star_available = available
    payload = "ON" if available else "OFF"
    logger.info("guide_star_available -> %s", payload)
    mqtt_client.publish(f"{BASE_TOPIC}/guide_star_available", payload, qos=0, retain=True)


# ----------------------------
# MQTT callbacks
# ----------------------------

def on_connect(client, userdata, flags, rc, properties=None):
    logger.info("Connected to MQTT broker with result code %s", rc)
    logger.debug("on_connect flags=%s properties=%s", flags, properties)
    if rc == 0:
        set_availability(True)
        publish_discovery()
    else:
        logger.error("MQTT connection failed with code %s", rc)


def on_disconnect(client, userdata, rc, properties=None):
    logger.info("Disconnected from MQTT broker with result code %s", rc)
    logger.debug("on_disconnect properties=%s", properties)
    # LWT handles offline state if disconnect is ungraceful.


# ----------------------------
# Setup MQTT client
# ----------------------------

def setup_mqtt():
    global mqtt_client

    logger.info("Setting up MQTT client")
    mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID, protocol=mqtt.MQTTv5)

    if MQTT_USERNAME is not None:
        logger.info("Using MQTT username authentication")
        mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect

    # LWT
    logger.info("Configuring MQTT LWT on topic %s", AVAILABILITY_TOPIC)
    mqtt_client.will_set(
        AVAILABILITY_TOPIC,
        payload="offline",
        qos=1,
        retain=True
    )

    logger.info("Connecting to MQTT broker at %s:%s", MQTT_HOST, MQTT_PORT)
    mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=MQTT_KEEPALIVE)
    mqtt_client.loop_start()
    logger.info("MQTT loop started")


# ----------------------------
# PHD2 reading loop
# ----------------------------

def read_phd2_events():
    """
    Connect to PHD2 server and process events.
    """
    global guide_star_available, PIXEL_SCALE_ARCSEC_PER_PX

    while True:
        sock = None
        try:
            logger.info("Connecting to PHD2 at %s:%s", PHD2_HOST, PHD2_PORT)
            sock = socket.create_connection((PHD2_HOST, PHD2_PORT))
            logger.info("Connected to PHD2 server")
            f = sock.makefile("rwb", buffering=0)

            # RPC ids for this connection
            APP_STATE_ID = 1
            PIXEL_SCALE_ID = 2

            # Optional sanity check: get_app_state
            req_app_state = {"method": "get_app_state", "id": APP_STATE_ID}
            req_line = json.dumps(req_app_state) + "\n"
            logger.debug("Sending RPC to PHD2: %s", req_app_state)
            f.write(req_line.encode("utf-8"))

            # Query pixel scale in arcsec/pixel
            req_pixel_scale = {"method": "get_pixel_scale", "id": PIXEL_SCALE_ID}
            req_line2 = json.dumps(req_pixel_scale) + "\n"
            logger.debug("Sending RPC to PHD2: %s", req_pixel_scale)
            f.write(req_line2.encode("utf-8"))

            # Reset pixel scale for this connection until we get a response
            PIXEL_SCALE_ARCSEC_PER_PX = None

            for raw_line in f:
                logger.debug("Raw line from PHD2 (%d bytes): %r", len(raw_line), raw_line)

                try:
                    line = raw_line.decode("utf-8").strip()
                except UnicodeDecodeError as e:
                    logger.warning("Unicode decode error for PHD2 line: %s", e)
                    continue

                if not line:
                    logger.debug("Skipping empty line from PHD2")
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning("JSON decode error for PHD2 line %r: %s", line, e)
                    continue

                logger.debug("Decoded PHD2 message: %s", msg)

                # RPC response
                if "result" in msg and "id" in msg:
                    rpc_id = msg.get("id")
                    result = msg.get("result")
                    logger.info("PHD2 RPC response id=%s result=%s", rpc_id, result)

                    if rpc_id == APP_STATE_ID:
                        logger.debug("App state: %s", result)
                    elif rpc_id == PIXEL_SCALE_ID:
                        # According to docs, result is number: guider image scale in arcsec/pixel
                        if isinstance(result, (int, float)):
                            PIXEL_SCALE_ARCSEC_PER_PX = float(result)
                            logger.info("Pixel scale set from PHD2: %s arcsec/px", PIXEL_SCALE_ARCSEC_PER_PX)
                        else:
                            logger.error("Unexpected get_pixel_scale result: %r", result)
                    else:
                        logger.debug("Unhandled RPC id %s", rpc_id)

                    continue

                evt = msg.get("Event")

                if evt is None:
                    logger.debug("PHD2 message without 'Event' and not RPC: %s", msg)
                    continue

                logger.debug("PHD2 event: %s", evt)

                if evt == "GuideStep":
                    ts = msg.get("Timestamp")

                    # According to your observation, treat RADistanceRaw / DECDistanceRaw as pixels
                    ra_err_px = msg.get("RADistanceRaw")
                    dec_err_px = msg.get("DECDistanceRaw")

                    snr = msg.get("SNR")
                    avg_dist = msg.get("AvgDist")
                    dx = msg.get("dx")
                    dy = msg.get("dy")

                    # Compute arcsecond errors using pixel scale if available
                    ra_arcsec = dec_arcsec = total_arcsec = None
                    if PIXEL_SCALE_ARCSEC_PER_PX is not None and ra_err_px is not None and dec_err_px is not None:
                        ra_arcsec = ra_err_px * PIXEL_SCALE_ARCSEC_PER_PX
                        dec_arcsec = dec_err_px * PIXEL_SCALE_ARCSEC_PER_PX
                        total_err_px = math.sqrt(ra_err_px**2 + dec_err_px**2)
                        total_arcsec = total_err_px * PIXEL_SCALE_ARCSEC_PER_PX
                    elif PIXEL_SCALE_ARCSEC_PER_PX is None:
                        logger.warning(
                            "Pixel scale not yet known, skipping RA/Dec/Total arcsec publish. "
                            "Raw px values: RA=%s Dec=%s", ra_err_px, dec_err_px
                        )

                    if ts is not None:
                        t = datetime.fromtimestamp(ts)
                    else:
                        t = None

                    logger.info(
                        "GuideStep at %s RA_px=%s Dec_px=%s RA_arcsec=%s Dec_arcsec=%s Total_arcsec=%s "
                        "dx=%s dy=%s SNR=%s AvgDist=%s",
                        t, ra_err_px, dec_err_px, ra_arcsec, dec_arcsec, total_arcsec,
                        dx, dy, snr, avg_dist
                    )

                    # Publish MQTT states
                    # Only publish arcsec if pixel scale is known
                    if PIXEL_SCALE_ARCSEC_PER_PX is not None:
                        publish_numeric(f"{BASE_TOPIC}/ra_error_arcsec", ra_arcsec)
                        publish_numeric(f"{BASE_TOPIC}/dec_error_arcsec", dec_arcsec)
                        publish_numeric(f"{BASE_TOPIC}/total_error_arcsec", total_arcsec)

                    # Always publish pixel offsets and other metrics
                    publish_numeric(f"{BASE_TOPIC}/dx_px", dx)
                    publish_numeric(f"{BASE_TOPIC}/dy_px", dy)
                    publish_numeric(f"{BASE_TOPIC}/snr", snr)
                    publish_numeric(f"{BASE_TOPIC}/avg_dist", avg_dist)

                    # Guiding is active and star is found
                    publish_guide_star_available(True)

                elif evt == "StarLost":
                    logger.warning("StarLost event from PHD2: %s", msg)
                    publish_guide_star_available(False)

                else:
                    logger.debug("Unhandled PHD2 event type %s: %s", evt, msg)

        except (ConnectionRefusedError, OSError) as e:
            logger.error("PHD2 connection error: %s. Retrying in 5 seconds.", e)
            time.sleep(5)
        except KeyboardInterrupt:
            logger.info("Interrupted by user, exiting PHD2 loop")
            break
        finally:
            if sock is not None:
                try:
                    logger.info("Closing PHD2 socket")
                    sock.close()
                except Exception as e:
                    logger.debug("Error closing PHD2 socket: %s", e)


# ----------------------------
# Main
# ----------------------------

def main():
    setup_mqtt()
    try:
        read_phd2_events()
    finally:
        try:
            set_availability(False)
        except Exception as e:
            logger.debug("Error setting availability offline in shutdown: %s", e)
        if mqtt_client is not None:
            logger.info("Stopping MQTT loop and disconnecting")
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        logger.info("PHD2 MQTT bridge stopped")


if __name__ == "__main__":
    main()
