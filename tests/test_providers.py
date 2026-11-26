import unittest

from ochub import providers as P


class TestProviders(unittest.TestCase):
    def test_presets_have_no_keys(self):
        for p in P.FREE_PROVIDERS:
            self.assertNotIn("api_key", p)
            self.assertTrue(p["key_url"].startswith("https://"))

    def test_openrouter_free_filter(self):
        catalog = ["openrouter/free", "google/gemma-4-31b-it:free", "anthropic/claude-opus-4.5",
                   "nvidia/nemotron-3.5-lightning:free"]
        free = P.select_free_models("openrouter", catalog)
        self.assertIn("openrouter/free", free)
        self.assertIn("google/gemma-4-31b-it:free", free)
        self.assertNotIn("anthropic/claude-opus-4.5", free)
        self.assertEqual(free[0], "openrouter/free")

    def test_groq_curated(self):
        catalog = ["openai/gpt-oss-120b", "whisper-large-v3", "meta-llama/llama-prompt-guard-2-22m"]
        free = P.select_free_models("groq", catalog)
        self.assertEqual(free, ["openai/gpt-oss-120b"])  # whisper/guard excluded

    def test_google_curated(self):
        catalog = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"]
        free = P.select_free_models("google", catalog)
        self.assertIn("gemini-2.5-flash", free)
        self.assertNotIn("gemini-2.5-pro", free)

    def test_custom_returns_all(self):
        self.assertEqual(P.select_free_models("mylab", ["a", "b"]), ["a", "b"])

    def test_builtin(self):
        self.assertTrue(P.is_builtin("groq"))
        self.assertFalse(P.is_builtin("mylab"))


if __name__ == "__main__":
    unittest.main()
