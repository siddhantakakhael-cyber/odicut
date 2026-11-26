import unittest

from ochub.logbus import LogBus


class TestLogBus(unittest.TestCase):
    def test_registered_secret_redacted(self):
        b = LogBus()
        b.register_secrets(["gsk_supersecretkey_123456"])
        b.info("using key gsk_supersecretkey_123456 for groq")
        text = b.text()
        self.assertNotIn("supersecretkey", text)
        self.assertIn("***REDACTED***", text)

    def test_pattern_redaction(self):
        b = LogBus()
        b.info("Authorization: Bearer abcdef1234567890abcdef")
        b.info("api_key=sk-or-v1-abcdef1234567890abcdef")
        text = b.text()
        self.assertNotIn("abcdef1234567890", text)

    def test_levels_and_history(self):
        b = LogBus()
        b.step("try 1"); b.switch("a -> b"); b.ok("done")
        text = b.text()
        self.assertIn("[STEP]", text)
        self.assertIn("[SWITCH]", text)
        self.assertIn("[OK]", text)

    def test_subscriber(self):
        b = LogBus()
        got = []
        b.subscribe(lambda lv, m: got.append((lv, m)))
        b.warn("hello")
        self.assertEqual(got, [("WARN", "hello")])

    def test_clear(self):
        b = LogBus()
        b.info("x"); b.clear()
        self.assertEqual(b.text(), "")


if __name__ == "__main__":
    unittest.main()
