import json
import unittest

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


if __name__ == "__main__":
    unittest.main()
