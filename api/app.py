import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt
from fastapi import FastAPI
from fastapi.responses import JSONResponse


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("temperature-metrics-api")


def env(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    return value.strip() if value else default


MQTT_HOST = env("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(env("MQTT_PORT", "1883"))
MQTT_USERNAME = env("MQTT_USERNAME", "victron")
MQTT_PASSWORD = env("MQTT_PASSWORD", "change-me")
MQTT_BASE_TOPIC = env("MQTT_BASE_TOPIC", "van/temperature").rstrip("/")
MQTT_TOPIC = env("MQTT_TOPIC", f"{MQTT_BASE_TOPIC}/+")

SENSORS = {
    "ca_pique": {"name": "Ça pique", "address": "E3:EE:E4:14:FA:B0"},
    "avalanche_toit": {"name": "Avalanche Toit", "address": "9D:88:00:00:02:2C"},
    "fruit_storage": {"name": "Fruit Storage", "address": "49:22:11:08:18:64"},
    "tete_used": {"name": "Tête used", "address": "49:22:09:05:14:A1"},
}

state_lock = threading.Lock()
latest_sensors: dict[str, dict[str, Any]] = {}
last_message_timestamp: str | None = None
mqtt_connected = False
mqtt_client: mqtt.Client | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sensor_id_from_topic(topic: str) -> str | None:
    prefix = f"{MQTT_BASE_TOPIC}/"
    if not topic.startswith(prefix):
        return None
    sensor_id = topic[len(prefix) :]
    return sensor_id if sensor_id in SENSORS else None


def mqtt_reason_is_success(reason_code: Any) -> bool:
    if hasattr(reason_code, "is_failure"):
        return not reason_code.is_failure
    return reason_code == 0


def on_connect(
    client: mqtt.Client,
    userdata: Any,
    flags: Any,
    reason_code: Any,
    properties: Any,
) -> None:
    global mqtt_connected

    if mqtt_reason_is_success(reason_code):
        with state_lock:
            mqtt_connected = True
        client.subscribe(MQTT_TOPIC, qos=1)
        logger.info(
            "MQTT connected to %s:%s; subscribed to %s",
            MQTT_HOST,
            MQTT_PORT,
            MQTT_TOPIC,
        )
    else:
        with state_lock:
            mqtt_connected = False
        logger.error("MQTT connection failed: %s", reason_code)


def on_disconnect(
    client: mqtt.Client,
    userdata: Any,
    disconnect_flags: Any,
    reason_code: Any,
    properties: Any,
) -> None:
    global mqtt_connected

    with state_lock:
        mqtt_connected = False
    logger.warning("MQTT disconnected: %s", reason_code)


def on_message(client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
    global last_message_timestamp

    sensor_id = sensor_id_from_topic(message.topic)
    if sensor_id is None:
        return

    try:
        payload = json.loads(message.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring invalid MQTT payload on %s: %s", message.topic, exc)
        return

    if not isinstance(payload, dict):
        logger.warning("Ignoring MQTT payload on %s: JSON root is not an object", message.topic)
        return
    if "temperature" not in payload:
        logger.warning("Ignoring MQTT payload on %s: temperature is missing", message.topic)
        return

    received_at = utc_now()
    reading = payload.copy()
    reading["sensor_id"] = sensor_id
    reading["received_at"] = received_at

    with state_lock:
        latest_sensors[sensor_id] = reading
        last_message_timestamp = received_at
    logger.info("Updated %s from %s", sensor_id, message.topic)


def create_mqtt_client() -> mqtt.Client:
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="temperature-metrics-api",
    )
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=60)

    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    return client


def sensors_response() -> dict[str, Any]:
    with state_lock:
        readings = {key: value.copy() for key, value in latest_sensors.items()}
        updated_at = last_message_timestamp

    return {
        "timestamp": updated_at,
        "sensor_count": len(readings),
        "expected_sensor_count": len(SENSORS),
        "missing_sensors": [sensor_id for sensor_id in SENSORS if sensor_id not in readings],
        "sensors": readings,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global mqtt_client

    logger.info("Starting Temperature Metrics API")
    logger.info("MQTT: %s:%s", MQTT_HOST, MQTT_PORT)
    logger.info("Topic: %s", MQTT_TOPIC)

    mqtt_client = create_mqtt_client()
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
    mqtt_client.loop_start()

    try:
        yield
    finally:
        if mqtt_client:
            mqtt_client.disconnect()
            mqtt_client.loop_stop()


app = FastAPI(title="Temperature Metrics API", version="1.0.0", lifespan=lifespan)


@app.get("/api/sensors")
@app.get("/api/metrics")
def get_sensors() -> JSONResponse:
    response = sensors_response()
    if not response["sensors"]:
        return JSONResponse({"status": "waiting_for_mqtt_data", **response}, status_code=503)
    return JSONResponse(response)


@app.get("/api/sensors/{sensor_id}")
def get_sensor(sensor_id: str) -> JSONResponse:
    if sensor_id not in SENSORS:
        return JSONResponse(
            {"status": "unknown_sensor", "known_sensors": list(SENSORS)},
            status_code=404,
        )

    with state_lock:
        reading = latest_sensors.get(sensor_id)
        result = reading.copy() if reading else None
    if result is None:
        return JSONResponse(
            {"status": "waiting_for_mqtt_data", "sensor_id": sensor_id},
            status_code=503,
        )
    return JSONResponse(result)


@app.get("/api/health")
def get_health() -> dict[str, Any]:
    with state_lock:
        connected = mqtt_connected
        updated_at = last_message_timestamp
        sensor_count = len(latest_sensors)

    return {
        "status": "ok" if connected else "degraded",
        "mqtt_connected": connected,
        "last_message_timestamp": updated_at,
        "sensor_count": sensor_count,
        "expected_sensor_count": len(SENSORS),
    }
