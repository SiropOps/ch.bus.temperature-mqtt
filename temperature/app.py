"""GPIO DHT22 collector. All BLE acquisition lives in bluetooth-mqtt."""
import asyncio
import json
import os
import re
import signal
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt

def env(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    return value.strip() if value else default


@dataclass(frozen=True)
class Sensor:
    name: str
    address: str
    protocol: str


def topic_safe(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-z0-9_-]+", "_", value.lower().strip())
    return value.strip("_") or "device"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def valid_environment(temperature: float, humidity: float | None = None) -> bool:
    return -80 <= temperature <= 150 and (humidity is None or 0 <= humidity <= 100)


def publish_sensor(
    client: mqtt.Client,
    sensor: Sensor,
    values: dict[str, Any],
    rssi: int | None = None,
) -> None:
    device_topic = f"{MQTT_BASE_TOPIC}/{topic_safe(sensor.name)}"
    enriched = {
        "timestamp": utc_now(),
        "name": sensor.name,
        "address": sensor.address,
        "protocol": sensor.protocol,
        **values,
    }
    if rssi is not None:
        enriched["rssi"] = rssi
    message = json.dumps(enriched, ensure_ascii=False)
    client.publish(device_topic, message, qos=1, retain=True)
    client.publish(f"{device_topic}/availability", "online", qos=1, retain=True)
    for key, value in enriched.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            client.publish(
                f"{device_topic}/{key}", json.dumps(value, ensure_ascii=False), qos=1, retain=True
            )
    print(message, flush=True)


async def read_dht22(device: Any) -> dict[str, Any] | None:
    for attempt in range(1, DHT22_READ_ATTEMPTS + 1):
        try:
            temperature = device.temperature
            humidity = device.humidity
            if temperature is None or humidity is None:
                raise RuntimeError("incomplete DHT22 reading")

            if not valid_environment(temperature, humidity):
                raise RuntimeError(
                    f"invalid DHT22 reading: temperature={temperature}, "
                    f"humidity={humidity}"
                )
            return {
                "model": "DHT22",
                "temperature": round(temperature, 2),
                "humidity": round(humidity, 2),
            }
        except RuntimeError as exc:
            if attempt == DHT22_READ_ATTEMPTS:
                print(
                    f"WARNING: DHT22 read failed after {attempt} attempts: {exc}",
                    flush=True,
                )
                return None
            print(
                f"WARNING: DHT22 read attempt {attempt}/{DHT22_READ_ATTEMPTS} "
                f"failed: {exc}; retrying",
                flush=True,
            )
            await asyncio.sleep(DHT22_RETRY_DELAY_SECONDS)

    return None

DHT22_SENSOR = Sensor("DHT22", "GPIO D4", "dht22")
MQTT_HOST = env("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(env("MQTT_PORT", "1883"))
MQTT_USERNAME = env("MQTT_USERNAME", "")
MQTT_PASSWORD = env("MQTT_PASSWORD", "")
MQTT_BASE_TOPIC = env("MQTT_BASE_TOPIC", "van/temperature").rstrip("/")
MQTT_STATUS_TOPIC = f"{MQTT_BASE_TOPIC}/dht22/status"
READ_INTERVAL_SECONDS = int(env("READ_INTERVAL_SECONDS", "300"))
MISSED_CYCLES_BEFORE_OFFLINE = int(env("MISSED_CYCLES_BEFORE_OFFLINE", "3"))
DHT22_READ_ATTEMPTS = 5
DHT22_RETRY_DELAY_SECONDS = 2.0


async def publish_cycle(client, missed_cycles, dht22_device):
    values = await read_dht22(dht22_device)
    misses = 0 if values is not None else missed_cycles.get(DHT22_SENSOR.address, 0) + 1
    missed_cycles[DHT22_SENSOR.address] = misses
    if values is not None:
        publish_sensor(client, DHT22_SENSOR, values)
    elif misses >= MISSED_CYCLES_BEFORE_OFFLINE:
        client.publish(f"{MQTT_BASE_TOPIC}/dht22/availability", "offline", qos=1, retain=True)
    summary = {
        "timestamp": utc_now(), "found": int(values is not None), "expected": 1,
        "missing": [DHT22_SENSOR.name] if values is None else [],
        "offline": [DHT22_SENSOR.name] if misses >= MISSED_CYCLES_BEFORE_OFFLINE else [],
        "missed_cycles": {DHT22_SENSOR.name: misses},
    }
    client.publish(f"{MQTT_BASE_TOPIC}/dht22/scan", json.dumps(summary), qos=1, retain=True)


async def run(client, dht22_device):
    missed_cycles = {}
    try:
        while True:
            await publish_cycle(client, missed_cycles, dht22_device)
            await asyncio.sleep(READ_INTERVAL_SECONDS)
    finally:
        dht22_device.exit()


async def run_with_signals(client, dht22_device):
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(run(client, dht22_device))
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        if not stopping:
            stopping = True
            loop.call_soon_threadsafe(task.cancel)
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        await task
    except asyncio.CancelledError:
        if not stopping:
            raise
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        client.publish(MQTT_STATUS_TOPIC, "online", qos=1, retain=True)
        print("DHT22 MQTT connected", flush=True)


def main():
    if READ_INTERVAL_SECONDS <= 0 or MISSED_CYCLES_BEFORE_OFFLINE <= 0:
        raise ValueError("Read interval and missed cycles must be positive")
    # Lazy imports keep GPIO hardware unnecessary for automated tests and APIs.
    import adafruit_dht
    import board
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="temperature-dht22")
    client.on_connect = on_connect
    client.will_set(MQTT_STATUS_TOPIC, "offline", qos=1, retain=True)
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.max_queued_messages_set(200)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    device = adafruit_dht.DHT22(board.D4, use_pulseio=False)
    try:
        client.connect_async(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
        asyncio.run(run_with_signals(client, device))
    finally:
        if client.is_connected():
            info = client.publish(MQTT_STATUS_TOPIC, "offline", qos=1, retain=True)
            try:
                info.wait_for_publish(timeout=2)
            except (RuntimeError, ValueError):
                pass
        client.disconnect()
        client.loop_stop()
        device.exit()


if __name__ == "__main__":
    main()
