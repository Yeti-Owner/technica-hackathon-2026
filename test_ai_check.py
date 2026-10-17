"""Pure-logic tests for the passive AI image checker; no network or browser."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browserkit.ai_check import (
    DEFAULTS,
    RunawayBreaker,
    builtin_whitelist,
    is_configured,
    is_whitelisted,
    load_config,
    passes_min_size,
    priority_key,
    read_env,
    secret,
    should_mark,
    too_large,
    validate_config,
)
from browserkit.controller import ROOT, lab_urls


class AICheckTests(unittest.TestCase):
    def test_env_parser_strips_quotes_and_skips_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# comment\n\nAI_CHECK_SERVER_URL = \"http://1.2.3.4\"\n"
                "AI_CHECK_API_KEY = 'abc123'\n"
                "PLAIN = value with spaces \n"
                "NOEQUALS\n",
                encoding="utf-8",
            )
            values = read_env(path)
            self.assertEqual(values["AI_CHECK_SERVER_URL"], "http://1.2.3.4")
            self.assertEqual(values["AI_CHECK_API_KEY"], "abc123")
            self.assertEqual(values["PLAIN"], "value with spaces")
            self.assertNotIn("NOEQUALS", values)

    def test_environment_wins_over_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('AI_CHECK_SERVER_URL = "http://file"\n', encoding="utf-8")
            values = read_env(path)
            with patch.dict(os.environ, {"AI_CHECK_SERVER_URL": "http://environment"}, clear=True):
                self.assertEqual(secret("AI_CHECK_SERVER_URL", values), "http://environment")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(secret("AI_CHECK_SERVER_URL", values), "http://file")

    def test_placeholder_secrets_are_not_configured(self):
        self.assertTrue(is_configured("http://1.2.3.4", "abc"))
        self.assertFalse(is_configured("PUT VULTR URL HERE like http://1.2.3.4", "abc"))
        self.assertFalse(is_configured("https://1.2.3.4", "abc"))
        self.assertFalse(is_configured("http://1.2.3.4", "PUT HEX API KEY HERE"))
        self.assertFalse(is_configured("", "abc"))

    def test_config_validation_ignores_unknown_keys_and_bad_values(self):
        config = validate_config({
            "threshold": 85,
            "minSize": "not a number",
            "concurrency": 9,
            "whitelist": ["http://example.com", 7, ""],
            "unknown": "ignored",
        })
        self.assertEqual(config["threshold"], 85)
        self.assertEqual(config["minSize"], DEFAULTS["minSize"])
        self.assertEqual(config["concurrency"], DEFAULTS["concurrency"])
        self.assertEqual(config["whitelist"], ["http://example.com"])
        self.assertNotIn("unknown", config)

    def test_bad_json_returns_defaults_and_one_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ai-check.json"
            path.write_text("{broken", encoding="utf-8")
            config, error = load_config(path)
            self.assertEqual(config, DEFAULTS)
            self.assertTrue(error)
            path.write_text(json.dumps({"threshold": 60}), encoding="utf-8")
            config, error = load_config(path)
            self.assertIsNone(error)
            self.assertEqual(config["threshold"], 60)

    def test_missing_config_uses_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            config, error = load_config(Path(directory) / "missing.json")
            self.assertIsNone(error)
            self.assertEqual(config, DEFAULTS)

    def test_whitelist_prefix_matching(self):
        self.assertTrue(is_whitelisted("http://example.com/a", ["http://example.com"]))
        self.assertFalse(is_whitelisted("https://example.com/a", ["http://example.com"]))
        self.assertFalse(is_whitelisted("http://example.com/a", []))
        self.assertTrue(is_whitelisted("file:///C:/x/home.html", [], ["file:///C:/x/home.html"]))
        self.assertFalse(is_whitelisted("file:///C:/x/other.html", [], ["file:///C:/x/home.html"]))

    def test_builtin_pages_are_whitelisted(self):
        prefixes = builtin_whitelist()
        self.assertTrue(is_whitelisted((ROOT / "ui/home.html").as_uri(), [], prefixes))
        self.assertTrue(is_whitelisted((ROOT / "ui/practice.html").as_uri(), [], prefixes))
        self.assertFalse(is_whitelisted((ROOT / "ui/image-lab.html").as_uri(), [], prefixes))

    def test_min_size_requires_both_dimensions(self):
        self.assertTrue(passes_min_size(80, 80, 80))
        self.assertFalse(passes_min_size(79, 80, 80))
        self.assertFalse(passes_min_size(80, 79, 80))
        self.assertTrue(passes_min_size(1000, 1000, 80))

    def test_priority_is_area_descending_then_order(self):
        items = [(100, 2, 0), (400, 5, 1), (400, 1, 2)]
        keys = sorted(priority_key(*item) for item in items)
        self.assertEqual(keys[0], (-400, 1, 2))
        self.assertEqual(keys[1], (-400, 5, 1))
        self.assertEqual(keys[2], (-100, 2, 0))

    def test_threshold_band_and_inconclusive_guard(self):
        self.assertTrue(should_mark(70, "sightengine", 70))
        self.assertFalse(should_mark(69, "sightengine", 70))
        self.assertTrue(should_mark(70, "local", 70))
        self.assertFalse(should_mark(70, "inconclusive", 70))
        self.assertFalse(should_mark(50, "inconclusive", 70))
        self.assertFalse(should_mark(None, "sightengine", 70))

    def test_body_size_cap(self):
        limit = 15 * 1024 * 1024
        self.assertTrue(too_large(limit + 1, limit))
        self.assertFalse(too_large(limit, limit))
        self.assertFalse(too_large(10, limit))

    def test_breaker_trips_on_failure_streak_and_resets(self):
        breaker = RunawayBreaker(3, 5)
        self.assertFalse(breaker.failure())
        self.assertFalse(breaker.failure())
        self.assertTrue(breaker.failure())
        breaker.success()
        self.assertFalse(breaker.failure())

    def test_breaker_rate_window(self):
        breaker = RunawayBreaker(3, 5)
        for index in range(5):
            breaker.record_start(100 + index)
        self.assertTrue(breaker.rate_exceeded(104))
        self.assertFalse(breaker.rate_exceeded(200))
        breaker.reset()
        self.assertFalse(breaker.rate_exceeded(200))

    def test_lab_urls_skips_bad_lines_and_dedupes(self):
        text = (
            "https://a.example/one.jpg\n"
            "http://b.example/two.png\n"
            "# comment line\n"
            "\n"
            "ftp://x.example/bad.jpg\n"
            "https://a.example/one.jpg\n"
            "https://c.example/three.webp\n"
        )
        self.assertEqual(lab_urls(text), [
            "https://a.example/one.jpg",
            "http://b.example/two.png",
            "https://c.example/three.webp",
        ])

    def test_lab_urls_empty_text(self):
        self.assertEqual(lab_urls(""), [])


if __name__ == "__main__":
    unittest.main()
