import json
import unittest
import os
from unittest.mock import patch
from fastapi.testclient import TestClient

import app


class FakeMessage:
    def __init__(self, topic: str, payload: object, retain: bool = False):
        self.topic = topic
        self.payload = json.dumps(payload).encode("utf-8")
        self.retain = retain


class TemperatureHistoryTest(unittest.TestCase):
    def setUp(self):
        with app.state_lock:
            app.latest_sensors.clear()
            app.temperature_history.clear()
            app.last_message_timestamp = None

    def test_each_mqtt_reading_is_kept_in_order(self):
        app.on_message(None, None, FakeMessage(
            "van/temperature/ca_pique", {"temperature": 12.5}
        ))
        app.on_message(None, None, FakeMessage(
            "van/temperature/ca_pique", {"temperature": 13.0}
        ))

        history = app.history_response()

        self.assertEqual(history["reading_count"], 2)
        self.assertEqual(
            [reading["temperature"] for reading in history["readings"]],
            [12.5, 13.0],
        )
        self.assertTrue(all("received_at" in reading for reading in history["readings"]))

    def test_history_can_be_filtered_by_sensor(self):
        app.on_message(None, None, FakeMessage(
            "van/temperature/ca_pique", {"temperature": 12.5}
        ))
        app.on_message(None, None, FakeMessage(
            "van/temperature/dht22", {"temperature": 18.2, "humidity": 42}
        ))

        history = app.history_response("dht22")

        self.assertEqual(history["reading_count"], 1)
        self.assertEqual(history["sensor_id"], "dht22")
        self.assertEqual(history["readings"][0]["humidity"], 42)

    def test_invalid_messages_are_not_saved(self):
        app.on_message(None, None, FakeMessage(
            "van/temperature/ca_pique", {"humidity": 55}
        ))

        self.assertEqual(app.history_response()["reading_count"], 0)

    def test_retained_value_is_latest_but_not_added_to_new_trip(self):
        app.on_message(None, None, FakeMessage(
            "van/temperature/ca_pique", {"temperature": 9.0}, retain=True
        ))

        self.assertEqual(app.latest_sensors["ca_pique"]["temperature"], 9.0)
        self.assertEqual(app.history_response()["reading_count"], 0)



class TemperatureApiCompatibilityTest(unittest.TestCase):
    def setUp(self):
        app.latest_sensors.clear()
        app.temperature_history.clear()
        self.client = TestClient(app.app)

    def test_gateway_and_gpio_readings_keep_routes_and_history(self):
        for sensor_id in app.SENSORS:
            app.on_message(None,None,FakeMessage(f"van/temperature/{sensor_id}",{"temperature":12.5,"humidity":42}))
            self.assertEqual(self.client.get(f"/api/sensors/{sensor_id}").status_code,200)
            self.assertEqual(self.client.get(f"/api/history/{sensor_id}").json()["reading_count"],1)
        self.assertEqual(self.client.get("/api/metrics").json()["sensor_count"],5)
        self.assertEqual(self.client.get("/api/sensors").json()["missing_sensors"],[])
        self.assertEqual(self.client.get("/api/history").json()["reading_count"],5)
        self.assertEqual(self.client.get("/api/sensors/unknown").status_code,404)

    def test_waiting_response_stays_503(self):
        self.assertEqual(self.client.get("/api/sensors").status_code,503)
        self.assertEqual(self.client.get("/api/sensors/ca_pique").status_code,503)

    def test_custom_gateway_sensor_config_is_supported_by_api(self):
        with patch.dict(os.environ,{"TEMPERATURE_SENSORS": '[{"name":"Réserve","address":"AA:BB:CC:DD:EE:FF","protocol":"ruuvi"}]'}):
            sensors = app.configured_sensors(app.SENSORS)
        self.assertEqual(set(sensors),{"reserve","dht22"})
        self.assertEqual(sensors["reserve"]["name"],"Réserve")

if __name__ == "__main__":
    unittest.main()
