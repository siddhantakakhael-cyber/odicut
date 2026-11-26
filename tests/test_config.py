import json
import tempfile
import unittest
from pathlib import Path

from ochub.config import Config


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Config(Path(tmp))
            self.assertTrue(c.router["auto_switch"])
            self.assertEqual(c.prefs["opencode_port"], 4096)
            self.assertEqual(c.providers, {})

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Config(Path(tmp))
            c.data["providers"]["groq"] = {"api_key": "gsk_secret_123456789", "enabled": True}
            c.data["router"]["cooldown_seconds"] = 90
            c.save()
            c2 = Config(Path(tmp))  # restart
            self.assertEqual(c2.providers["groq"]["api_key"], "gsk_secret_123456789")
            self.assertEqual(c2.router["cooldown_seconds"], 90)

    def test_masked(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Config(Path(tmp))
            c.data["providers"]["groq"] = {"api_key": "gsk_supersecret_999"}
            m = c.masked()
            self.assertNotIn("supersecret", json.dumps(m))
            self.assertEqual(m["providers"]["groq"]["api_key"], "••••_999")
            self.assertIn("gsk_supersecret_999", c.secrets())

    def test_corrupt_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config.json").write_text("{bad json")
            c = Config(Path(tmp))
            self.assertTrue(c.router["auto_switch"])

    def test_no_hardcoded_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Config(Path(tmp))
            self.assertEqual(c.secrets(), [])


if __name__ == "__main__":
    unittest.main()
