import asyncio
import json
import os
import re
import struct
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
import adafruit_dht
import board
import paho.mqtt.client as mqtt


def env(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    return value.strip() if value else default


@dataclass(frozen=True)
class Sensor:
    name: str
    address: str
    protocol: str


SENSORS = (
    Sensor("Ça pique", "E3:EE:E4:14:FA:B0", "ruuvi"),
    Sensor("Avalanche Toit", "9D:88:00:00:02:2C", "sensorblue"),
    Sensor("Fruit Storage", "49:22:11:08:18:64", "inkbird"),
    Sensor("Tête used", "49:22:09:05:14:A1", "inkbird"),
)
DHT22_SENSOR = Sensor("DHT22", "GPIO D4", "dht22")

MQTT_HOST = env("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(env("MQTT_PORT", "1883"))
MQTT_USERNAME = env("MQTT_USERNAME", "")
MQTT_PASSWORD = env("MQTT_PASSWORD", "")
MQTT_BASE_TOPIC = env("MQTT_BASE_TOPIC", "van/temperature").rstrip("/")
MQTT_STATUS_TOPIC = f"{MQTT_BASE_TOPIC}/status"
READ_INTERVAL_SECONDS = int(env("READ_INTERVAL_SECONDS", "300"))
SCAN_TIMEOUT_SECONDS = float(env("SCAN_TIMEOUT_SECONDS", "45"))
MISSED_CYCLES_BEFORE_OFFLINE = int(env("MISSED_CYCLES_BEFORE_OFFLINE", "3"))
DHT22_READ_ATTEMPTS = 5
DHT22_RETRY_DELAY_SECONDS = 2.0

THERMOBEACON_MANUFACTURER_IDS = {0x10, 0x11, 0x14, 0x15, 0x18, 0x1B, 0x30}
PARSERS: dict[str, Callable[[AdvertisementData], dict[str, Any] | None]] = {}


def topic_safe(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-z0-9_-]+", "_", value.lower().strip())
    return value.strip("_") or "device"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def valid_environment(temperature: float, humidity: float | None = None) -> bool:
    return -80 <= temperature <= 150 and (humidity is None or 0 <= humidity <= 100)


def parse_ruuvi(advertisement: AdvertisementData) -> dict[str, Any] | None:
    data = advertisement.manufacturer_data.get(0x0499)
    if not data:
        return None

    if data[0] == 5 and len(data) >= 24:
        temperature_raw = int.from_bytes(data[1:3], "big", signed=True)
        humidity_raw = int.from_bytes(data[3:5], "big")
        pressure_raw = int.from_bytes(data[5:7], "big")
        power_raw = int.from_bytes(data[13:15], "big")
        values = {
            "model": "RuuviTag",
            "data_format": 5,
            "temperature": round(temperature_raw * 0.005, 3),
            "humidity": round(humidity_raw * 0.0025, 4),
            "pressure": pressure_raw + 50000,
            "acceleration_x": int.from_bytes(data[7:9], "big", signed=True),
            "acceleration_y": int.from_bytes(data[9:11], "big", signed=True),
            "acceleration_z": int.from_bytes(data[11:13], "big", signed=True),
            "battery_voltage": ((power_raw >> 5) + 1600) / 1000,
            "tx_power": (power_raw & 0x1F) * 2 - 40,
            "movement_counter": data[15],
            "measurement_sequence": int.from_bytes(data[16:18], "big"),
        }
    elif data[0] == 3 and len(data) >= 14:
        temperature = data[2] + data[3] / 100
        if data[2] & 0x80:
            temperature = -(data[2] & 0x7F) - data[3] / 100
        values = {
            "model": "RuuviTag",
            "data_format": 3,
            "temperature": round(temperature, 2),
            "humidity": data[1] * 0.5,
            "pressure": int.from_bytes(data[4:6], "big") + 50000,
            "acceleration_x": int.from_bytes(data[6:8], "big", signed=True),
            "acceleration_y": int.from_bytes(data[8:10], "big", signed=True),
            "acceleration_z": int.from_bytes(data[10:12], "big", signed=True),
            "battery_voltage": int.from_bytes(data[12:14], "big") / 1000,
        }
    else:
        return None

    if not valid_environment(values["temperature"], values["humidity"]):
        return None
    return values


def parse_sensorblue(advertisement: AdvertisementData) -> dict[str, Any] | None:
    for manufacturer_id, payload in advertisement.manufacturer_data.items():
        if manufacturer_id not in THERMOBEACON_MANUFACTURER_IDS:
            continue
        data = manufacturer_id.to_bytes(2, "little") + payload
        if len(data) != 20:
            continue

        voltage_mv, temperature_raw, humidity_raw = struct.unpack("<HhH", data[10:16])
        temperature = temperature_raw / 16
        humidity = humidity_raw / 16
        if not valid_environment(temperature, humidity):
            continue

        if voltage_mv >= 3000:
            battery = 100
        elif voltage_mv >= 2600:
            battery = 60 + (voltage_mv - 2600) * 0.1
        elif voltage_mv >= 2500:
            battery = 40 + (voltage_mv - 2500) * 0.2
        elif voltage_mv >= 2450:
            battery = 20 + (voltage_mv - 2450) * 0.4
        else:
            battery = 0

        return {
            "model": "SensorBlue/ThermoBeacon",
            "temperature": round(temperature, 2),
            "humidity": round(humidity, 2),
            "battery": round(battery),
            "battery_voltage": round(voltage_mv / 1000, 3),
            "button_pressed": bool(data[3] & 0x80),
        }
    return None


def parse_inkbird(advertisement: AdvertisementData) -> dict[str, Any] | None:
    for manufacturer_id, payload in advertisement.manufacturer_data.items():
        data = manufacturer_id.to_bytes(2, "little") + payload
        if len(data) == 9:
            temperature_raw, humidity_raw = struct.unpack("<hH", data[0:4])
            temperature = temperature_raw / 100
            humidity = humidity_raw / 100
            battery = data[7]
            model = "Inkbird IBS-TH/IBS-TH2"
        elif len(data) == 18:
            temperature_raw, humidity_raw = struct.unpack("<hH", data[6:10])
            temperature = temperature_raw / 10
            humidity = humidity_raw / 10
            battery = data[10]
            model = "Inkbird IBS-TH (18-byte)"
        else:
            continue

        if not valid_environment(temperature, humidity) or not 0 <= battery <= 100:
            continue
        values: dict[str, Any] = {
            "model": model,
            "temperature": round(temperature, 2),
            "battery": battery,
        }
        if humidity_raw != 0:
            values["humidity"] = round(humidity, 2)
        return values
    return None


PARSERS.update(
    ruuvi=parse_ruuvi,
    sensorblue=parse_sensorblue,
    inkbird=parse_inkbird,
)


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


async def publish_cycle(
    client: mqtt.Client,
    readings: dict[str, tuple[Sensor, dict[str, Any], int]],
    missed_cycles: dict[str, int],
    dht22_device: Any,
) -> None:
    for sensor, values, rssi in readings.values():
        publish_sensor(client, sensor, values, rssi)
        missed_cycles[sensor.address.upper()] = 0

    missing = [sensor for sensor in SENSORS if sensor.address.upper() not in readings]
    for sensor in missing:
        address = sensor.address.upper()
        missed_cycles[address] += 1
        misses = missed_cycles[address]
        if misses >= MISSED_CYCLES_BEFORE_OFFLINE:
            print(
                f"WARNING: no valid BLE advertisement from {sensor.name} "
                f"({sensor.address}) for {misses} consecutive cycles; marking offline",
                flush=True,
            )
            client.publish(
                f"{MQTT_BASE_TOPIC}/{topic_safe(sensor.name)}/availability",
                "offline",
                qos=1,
                retain=True,
            )
        else:
            print(
                f"WARNING: no valid BLE advertisement from {sensor.name} "
                f"({sensor.address}); missed cycle {misses}/"
                f"{MISSED_CYCLES_BEFORE_OFFLINE}, keeping previous availability",
                flush=True,
            )

    dht22_values = await read_dht22(dht22_device)
    if dht22_values is not None:
        publish_sensor(client, DHT22_SENSOR, dht22_values)
        missed_cycles[DHT22_SENSOR.address] = 0
    else:
        missed_cycles[DHT22_SENSOR.address] += 1
        if missed_cycles[DHT22_SENSOR.address] >= MISSED_CYCLES_BEFORE_OFFLINE:
            client.publish(
                f"{MQTT_BASE_TOPIC}/{topic_safe(DHT22_SENSOR.name)}/availability",
                "offline",
                qos=1,
                retain=True,
            )

    summary = {
        "timestamp": utc_now(),
        "found": len(readings) + (1 if dht22_values is not None else 0),
        "expected": len(SENSORS) + 1,
        "missing": [sensor.name for sensor in missing],
        "offline": [
            sensor.name
            for sensor in missing
            if missed_cycles[sensor.address.upper()] >= MISSED_CYCLES_BEFORE_OFFLINE
        ],
        "missed_cycles": {
            sensor.name: missed_cycles[sensor.address.upper()] for sensor in SENSORS
        },
    }
    if dht22_values is None:
        summary["missing"].append(DHT22_SENSOR.name)
        if missed_cycles[DHT22_SENSOR.address] >= MISSED_CYCLES_BEFORE_OFFLINE:
            summary["offline"].append(DHT22_SENSOR.name)
    summary["missed_cycles"][DHT22_SENSOR.name] = missed_cycles[DHT22_SENSOR.address]
    client.publish(
        f"{MQTT_BASE_TOPIC}/scan",
        json.dumps(summary, ensure_ascii=False),
        qos=1,
        retain=True,
    )


def on_connect(client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
    if reason_code == 0:
        client.publish(MQTT_STATUS_TOPIC, "online", qos=1, retain=True)
        print(f"MQTT connected; published {MQTT_STATUS_TOPIC}=online", flush=True)
    else:
        print(f"MQTT connection failed: {reason_code}", flush=True)


def connect_mqtt(client: mqtt.Client) -> None:
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            return
        except OSError as exc:
            print(f"ERROR: MQTT connect failed: {exc}", flush=True)
            time.sleep(min(READ_INTERVAL_SECONDS, 30))


async def run(client: mqtt.Client) -> None:
    sensors_by_address = {sensor.address.upper(): sensor for sensor in SENSORS}
    readings: dict[str, tuple[Sensor, dict[str, Any], int]] = {}
    missed_cycles = {address: 0 for address in sensors_by_address}
    missed_cycles[DHT22_SENSOR.address] = 0
    all_found = asyncio.Event()
    dht22_device = adafruit_dht.DHT22(board.D4, use_pulseio=False)

    def on_advertisement(
        device: BLEDevice, advertisement: AdvertisementData
    ) -> None:
        address = device.address.upper()
        sensor = sensors_by_address.get(address)
        if sensor is None:
            return
        try:
            values = PARSERS[sensor.protocol](advertisement)
        except (IndexError, KeyError, struct.error, ValueError) as exc:
            print(f"Invalid BLE data from {sensor.name}: {exc}", flush=True)
            return
        if values is None:
            return
        readings[address] = (sensor, values, advertisement.rssi)
        if len(readings) == len(SENSORS):
            all_found.set()

    scanner = BleakScanner(detection_callback=on_advertisement)
    cycle_started = time.monotonic()
    next_publish = cycle_started + READ_INTERVAL_SECONDS
    await scanner.start()
    print("BLE scanner active continuously", flush=True)
    try:
        try:
            await asyncio.wait_for(all_found.wait(), timeout=SCAN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            pass

        initial_readings = readings.copy()
        readings.clear()
        await publish_cycle(client, initial_readings, missed_cycles, dht22_device)

        while True:
            await asyncio.sleep(max(0, next_publish - time.monotonic()))
            cycle_readings = readings.copy()
            readings.clear()
            await publish_cycle(client, cycle_readings, missed_cycles, dht22_device)
            next_publish += READ_INTERVAL_SECONDS
            if next_publish <= time.monotonic():
                next_publish = time.monotonic() + READ_INTERVAL_SECONDS
    finally:
        await scanner.stop()
        dht22_device.exit()


def main() -> None:
    if (
        READ_INTERVAL_SECONDS <= 0
        or SCAN_TIMEOUT_SECONDS <= 0
        or MISSED_CYCLES_BEFORE_OFFLINE <= 0
    ):
        raise ValueError(
            "READ_INTERVAL_SECONDS, SCAN_TIMEOUT_SECONDS and "
            "MISSED_CYCLES_BEFORE_OFFLINE must be positive"
        )

    print("Starting temperature BLE/DHT22 -> MQTT bridge", flush=True)
    print(f"MQTT: {MQTT_HOST}:{MQTT_PORT}", flush=True)
    print(f"Topic base: {MQTT_BASE_TOPIC}", flush=True)
    print(
        f"Publication cycle: {READ_INTERVAL_SECONDS}s; "
        f"initial BLE wait: up to {SCAN_TIMEOUT_SECONDS}s",
        flush=True,
    )
    print(
        f"Offline after {MISSED_CYCLES_BEFORE_OFFLINE} consecutive missed cycles",
        flush=True,
    )
    for sensor in SENSORS:
        print(f"Sensor: {sensor.name} ({sensor.address}, {sensor.protocol})", flush=True)
    print(f"Sensor: {DHT22_SENSOR.name} ({DHT22_SENSOR.address})", flush=True)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.will_set(MQTT_STATUS_TOPIC, "offline", qos=1, retain=True)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    connect_mqtt(client)
    client.loop_start()
    try:
        asyncio.run(run(client))
    finally:
        client.publish(MQTT_STATUS_TOPIC, "offline", qos=1, retain=True)
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
