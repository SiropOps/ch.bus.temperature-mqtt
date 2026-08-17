import asyncio
import ast
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


APP_PATH = Path(__file__).with_name("app.py")


def load_parser_functions() -> dict[str, Any]:
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {
        "valid_environment",
        "parse_inkbird",
        "parse_inkbird_gatt",
        "read_inkbird_gatt",
    }
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in wanted
    ]
    module = ast.Module(body=functions, type_ignores=[])
    namespace: dict[str, Any] = {
        "AdvertisementData": Any,
        "BLEDevice": Any,
        "BleakClient": None,
        "Any": Any,
        "INKBIRD_DATA_CHARACTERISTIC_UUID": "test-fff2",
        "INKBIRD_GATT_TIMEOUT_SECONDS": 20,
        "struct": struct,
    }
    exec(compile(module, APP_PATH, "exec"), namespace)
    return namespace


class ParseInkbirdTest(unittest.TestCase):
    def test_uses_latest_accumulated_manufacturer_data(self) -> None:
        parse_inkbird = load_parser_functions()["parse_inkbird"]
        advertisement = SimpleNamespace(
            manufacturer_data={
                # Old observation: 7.70 C, no humidity, 24% battery.
                770: b"\x00\x00\x00\x00\x00\x18\x00",
                # Latest observation: 7.90 C, no humidity, 24% battery.
                790: b"\x00\x00\x00\x00\x00\x18\x00",
            }
        )

        values = parse_inkbird(advertisement)

        self.assertIsNotNone(values)
        self.assertEqual(values["temperature"], 7.9)
        self.assertEqual(values["battery"], 24)

    def test_gatt_read_corrects_return_to_an_already_seen_temperature(self) -> None:
        functions = load_parser_functions()
        parse_inkbird = functions["parse_inkbird"]
        parse_inkbird_gatt = functions["parse_inkbird_gatt"]
        advertisement = SimpleNamespace(
            manufacturer_data={
                770: b"\x00\x00\x00\x00\x00\x18\x00",
                790: b"\x00\x00\x00\x00\x00\x18\x00",
            }
        )

        accumulated_values = parse_inkbird(advertisement)
        current_values = parse_inkbird_gatt(
            struct.pack("<hH", 770, 0), accumulated_values
        )

        self.assertEqual(accumulated_values["temperature"], 7.9)
        self.assertEqual(current_values["temperature"], 7.7)
        self.assertEqual(current_values["battery"], 24)
        self.assertNotIn("humidity", current_values)

    def test_gatt_values_do_not_leak_between_inkbird_sensors(self) -> None:
        parse_inkbird_gatt = load_parser_functions()["parse_inkbird_gatt"]

        fruit_storage = parse_inkbird_gatt(
            struct.pack("<hH", 835, 4512), {"battery": 24}
        )
        tete_used = parse_inkbird_gatt(
            struct.pack("<hH", 1270, 0), {"battery": 63}
        )

        self.assertEqual(fruit_storage["temperature"], 8.35)
        self.assertEqual(fruit_storage["humidity"], 45.12)
        self.assertEqual(fruit_storage["battery"], 24)
        self.assertEqual(tete_used["temperature"], 12.7)
        self.assertNotIn("humidity", tete_used)
        self.assertEqual(tete_used["battery"], 63)

    def test_rejects_truncated_or_invalid_gatt_data(self) -> None:
        parse_inkbird_gatt = load_parser_functions()["parse_inkbird_gatt"]

        self.assertIsNone(parse_inkbird_gatt(b"\x01\x02\x03", {}))
        self.assertIsNone(
            parse_inkbird_gatt(struct.pack("<hH", 2000, 10001), {})
        )

    def test_direct_read_uses_fff2_and_decodes_its_current_value(self) -> None:
        functions = load_parser_functions()

        class FakeBleakClient:
            characteristic = None
            timeout = None

            def __init__(self, device, timeout):
                self.device = device
                FakeBleakClient.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return None

            async def read_gatt_char(self, characteristic):
                FakeBleakClient.characteristic = characteristic
                return bytearray(struct.pack("<hH", 770, 0))

        functions["BleakClient"] = FakeBleakClient
        values = asyncio.run(
            functions["read_inkbird_gatt"](object(), {"battery": 24})
        )

        self.assertEqual(FakeBleakClient.characteristic, "test-fff2")
        self.assertEqual(FakeBleakClient.timeout, 20)
        self.assertEqual(values["temperature"], 7.7)
        self.assertEqual(values["battery"], 24)


if __name__ == "__main__":
    unittest.main()
