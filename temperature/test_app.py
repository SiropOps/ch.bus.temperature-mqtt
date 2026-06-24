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
    wanted = {"valid_environment", "parse_inkbird"}
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    module = ast.Module(body=functions, type_ignores=[])
    namespace: dict[str, Any] = {
        "AdvertisementData": Any,
        "Any": Any,
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


if __name__ == "__main__":
    unittest.main()
