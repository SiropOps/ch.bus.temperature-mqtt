import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app


class Dht22Tests(unittest.IsolatedAsyncioTestCase):
    async def test_gpio_reading_keeps_json_and_scalar_topics(self):
        client = Mock()
        device = SimpleNamespace(temperature=18.25, humidity=42.5)
        missed = {}
        await app.publish_cycle(client, missed, device)
        payloads = {call.args[0]:call.args[1] for call in client.publish.call_args_list}
        reading = json.loads(payloads["van/temperature/dht22"])
        self.assertEqual(reading["temperature"],18.25)
        self.assertEqual(reading["humidity"],42.5)
        self.assertEqual(reading["protocol"],"dht22")
        self.assertEqual(reading["address"],"GPIO D4")
        self.assertEqual(payloads["van/temperature/dht22/availability"],"online")
        self.assertEqual(json.loads(payloads["van/temperature/dht22/temperature"]),18.25)
        self.assertEqual(missed[app.DHT22_SENSOR.address],0)
        self.assertNotIn("van/temperature/scan",payloads)

    async def test_failed_sensor_is_offline_after_missed_cycles(self):
        client = Mock()
        device = SimpleNamespace(temperature=None, humidity=None)
        missed = {}
        with patch.object(app,"DHT22_READ_ATTEMPTS",1), patch.object(app,"MISSED_CYCLES_BEFORE_OFFLINE",2):
            await app.publish_cycle(client, missed, device)
            self.assertFalse(any(call.args[0].endswith("availability") for call in client.publish.call_args_list))
            await app.publish_cycle(client, missed, device)
        client.publish.assert_any_call("van/temperature/dht22/availability","offline",qos=1,retain=True)

    async def test_crc_error_is_retried(self):
        class Device:
            reads = 0
            humidity = 45.0
            @property
            def temperature(self):
                self.reads += 1
                if self.reads == 1:
                    raise RuntimeError("checksum failed")
                return 21.5
        with patch.object(app,"DHT22_RETRY_DELAY_SECONDS",0):
            result = await app.read_dht22(Device())
        self.assertEqual(result["temperature"],21.5)

    async def test_cancel_releases_gpio(self):
        device = SimpleNamespace(temperature=20,humidity=40,exit=Mock())
        task = asyncio.create_task(app.run(Mock(),device))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        device.exit.assert_called_once()

    def test_dht_status_does_not_compete_with_gateway(self):
        self.assertEqual(app.MQTT_STATUS_TOPIC,"van/temperature/dht22/status")


if __name__ == "__main__":
    unittest.main()
