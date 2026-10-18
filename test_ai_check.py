"""Pure-logic tests for the passive AI image checker; no network or browser."""
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from browserkit.ai_check import (
    AICheck,
    DEFAULTS,
    RunawayBreaker,
    builtin_image_whitelist,
    builtin_whitelist,
    is_configured,
    is_image_whitelisted,
    is_whitelisted,
    load_config,
    passes_min_size,
    post_mode,
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

    def test_max_attempts_per_image_default_and_clamp(self):
        self.assertEqual(DEFAULTS["maxAttemptsPerImage"], 2)
        self.assertEqual(validate_config({})["maxAttemptsPerImage"], 2)
        self.assertEqual(validate_config({"maxAttemptsPerImage": 3})["maxAttemptsPerImage"], 3)
        self.assertEqual(validate_config({"maxAttemptsPerImage": 5})["maxAttemptsPerImage"], 5)
        self.assertEqual(validate_config({"maxAttemptsPerImage": 0})["maxAttemptsPerImage"], 2)
        self.assertEqual(validate_config({"maxAttemptsPerImage": 6})["maxAttemptsPerImage"], 2)
        self.assertEqual(validate_config({"maxAttemptsPerImage": "x"})["maxAttemptsPerImage"], 2)

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

    def test_config_validation_keeps_image_whitelist_strings(self):
        config = validate_config({"imageWhitelist": ["http://example.com", 7, ""]})
        self.assertEqual(config["imageWhitelist"], ["http://example.com"])
        self.assertEqual(validate_config({})["imageWhitelist"], [])

    def test_builtin_image_whitelist_hashes_project_images(self):
        hashes, prefixes = builtin_image_whitelist()
        for rel in ("assets/sun.png", "ui/assets/nod-logo.png", "Images for ui/moon.png"):
            expected = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            self.assertIn(expected, hashes)
        self.assertTrue(is_image_whitelisted(
            (ROOT / "ui/assets/clouds/night-clouds-1.png").as_uri(), None, hashes, prefixes))
        self.assertTrue(is_image_whitelisted(
            (ROOT / "Images for ui/Cloud 1.png").as_uri(), None, hashes, prefixes))
        self.assertFalse(is_image_whitelisted(
            (ROOT / "scratch/test_logo_cloud.png").as_uri(), None, hashes, prefixes))
        self.assertFalse(is_image_whitelisted("http://example.com/x.png", None, hashes, prefixes))

    def test_is_image_whitelisted_hash_prefix_and_empty(self):
        hashes = {"a" * 64}
        prefixes = ["file:///C:/x/assets/"]
        self.assertTrue(is_image_whitelisted("http://example.com/x.png", "a" * 64, hashes, prefixes))
        self.assertTrue(is_image_whitelisted(None, "a" * 64, hashes, prefixes))
        self.assertTrue(is_image_whitelisted("file:///C:/x/assets/y.png", None, hashes, prefixes))
        self.assertFalse(is_image_whitelisted("http://example.com/x.png", None, set(), []))
        self.assertFalse(is_image_whitelisted(None, None, hashes, prefixes))
        self.assertTrue(is_image_whitelisted(
            "http://example.com/x.png", None, set(), [], ["http://example.com"]))

    def test_ai_check_image_whitelist_method_honors_config(self):
        ai = AICheck(browser_controller=None)
        ai.config = dict(DEFAULTS)
        ai.config["imageWhitelist"] = ["http://example.com/owned"]
        self.assertTrue(ai.is_image_whitelisted("http://example.com/owned/x.png"))
        self.assertFalse(ai.is_image_whitelisted("http://example.com/other/x.png"))

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

    def test_real_test_images_file_parses_and_dedupes(self):
        """Regression guard: the shipped lab list stays parseable and deduped."""
        text = (ROOT / "test_images.txt").read_text(encoding="utf-8")
        urls = lab_urls(text)
        self.assertEqual(len(urls), 13)  # update when the lab list is re-curated
        self.assertEqual(len(set(urls)), len(urls))
        self.assertEqual(
            urls.count("https://sightengine.com/assets/img/genai/gpt-220w.webp"), 1
        )


class PostModeTests(unittest.TestCase):
    def test_post_mode_prefers_file_then_url(self):
        self.assertEqual(post_mode(b"bytes", "http://example.com/x.jpg"), "file")
        self.assertEqual(post_mode(None, "http://example.com/x.jpg"), "url")
        self.assertEqual(post_mode(None, ""), "skip")
        self.assertEqual(post_mode(None, None), "skip")


class _FakePage:
    """Just enough of a page for whitelist checks and reporting."""

    def __init__(self, url="http://example.com/page.html"):
        self.url = url


class ProcessHardeningTests(unittest.IsolatedAsyncioTestCase):
    """No network: the request layer is replaced with in-process fakes."""

    def make_ai(self):
        ai = AICheck(browser_controller=None)
        ai.config = dict(DEFAULTS)
        ai.enabled = True
        ai.paused = False
        ai.closed = False
        ai.server = "http://127.0.0.1:1"
        ai.key = "test-key"
        return ai

    async def test_inflight_cleared_after_post_failure(self):
        ai = self.make_ai()
        ai.config["retries"] = 0
        sha = "a" * 64
        ai.sniffed[sha] = b"image-bytes"

        def boom(data, src=None):
            raise RuntimeError("network down")

        ai._post = boom
        await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertNotIn(sha, ai.inflight)
        self.assertEqual(ai.counters["inflight"], 0)
        self.assertEqual(ai.counters["failed"], 1)
        self.assertEqual(ai.counters["scanned"], 0)

    async def test_process_falls_back_to_url_when_bytes_missing(self):
        ai = self.make_ai()
        sha = "b" * 64
        calls = {"url": [], "file": []}

        def post_url(url, src=None):
            calls["url"].append(url)
            return {"score": 10, "source": "sightengine"}

        def post_file(data, src=None):
            calls["file"].append(data)
            return {"score": 10, "source": "sightengine"}

        ai._post_url = post_url
        ai._post = post_file
        await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertEqual(calls["url"], ["http://example.com/x.jpg"])
        self.assertEqual(calls["file"], [])
        self.assertEqual(ai.counters["scanned"], 1)
        self.assertNotIn(sha, ai.inflight)

    async def test_process_uses_file_mode_when_bytes_present(self):
        ai = self.make_ai()
        sha = "c" * 64
        ai.sniffed[sha] = b"image-bytes"
        calls = {"url": [], "file": []}

        def post_url(url, src=None):
            calls["url"].append(url)
            return {"score": 10, "source": "sightengine"}

        def post_file(data, src=None):
            calls["file"].append(data)
            return {"score": 10, "source": "sightengine"}

        ai._post_url = post_url
        ai._post = post_file
        await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertEqual(calls["file"], [b"image-bytes"])
        self.assertEqual(calls["url"], [])
        self.assertEqual(ai.counters["scanned"], 1)

    async def test_worker_survives_a_raising_process(self):
        ai = self.make_ai()
        calls = {"n": 0}

        async def fake_next():
            calls["n"] += 1
            if calls["n"] == 1:
                return (None, "http://example.com/x.jpg", "d" * 64)
            ai.enabled = False
            return None

        async def fake_process(page, url, sha):
            raise RuntimeError("boom")

        ai._next = fake_next
        ai._process = fake_process
        await ai._worker()
        self.assertGreaterEqual(calls["n"], 2)
        self.assertEqual(ai.last_error, "boom")

    async def test_failed_post_requeues_then_success_counts_once(self):
        ai = self.make_ai()
        sha = "e" * 64
        ai.sniffed[sha] = b"image-bytes"
        calls = {"n": 0}

        def post(data, src=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("network down")
            return {"score": 90, "source": "sightengine"}

        ai._post = post
        await ai._process(None, "http://example.com/x.jpg", sha)
        # First attempt failed but a retry is allowed: still pending, not failed.
        self.assertEqual(ai.counters["failed"], 0)
        self.assertEqual(ai.counters["scanned"], 0)
        self.assertIn(sha, ai.pending_shas)
        self.assertIn((id(None), "http://example.com/x.jpg"), ai.pending_map)
        self.assertNotIn(sha, ai.inflight)
        # The retry succeeds and is counted exactly once.
        await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(ai.counters["scanned"], 1)
        self.assertEqual(ai.counters["failed"], 0)
        self.assertNotIn(sha, ai.pending_shas)
        self.assertEqual(ai.pending_map, {})
        self.assertNotIn(sha, ai.inflight)
        self.assertEqual(ai.counters["inflight"], 0)

    async def test_missing_bytes_uses_url_mode_on_retry(self):
        ai = self.make_ai()
        sha = "3" * 64  # no sniffed bytes: url mode both passes
        calls = {"url": 0}

        def post_url(url, src=None):
            calls["url"] += 1
            if calls["url"] == 1:
                raise RuntimeError("network down")
            return {"score": 10, "source": "sightengine"}

        ai._post_url = post_url
        await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertIn(sha, ai.pending_shas)
        await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertEqual(calls["url"], 2)
        self.assertEqual(ai.counters["scanned"], 1)

    async def test_retries_zero_fails_after_first_attempt(self):
        ai = self.make_ai()
        ai.config["retries"] = 0
        sha = "f" * 64
        ai.sniffed[sha] = b"image-bytes"

        def boom(data, src=None):
            raise RuntimeError("network down")

        ai._post = boom
        await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertEqual(ai.counters["failed"], 1)
        self.assertEqual(ai.counters["scanned"], 0)
        self.assertNotIn(sha, ai.pending_shas)
        self.assertNotIn(sha, ai.inflight)

    async def test_breaker_trip_keeps_queued_candidates(self):
        ai = self.make_ai()
        queued_sha = "1" * 64
        ai.priority[queued_sha] = (-100, 0)
        ai._requeue(None, "http://example.com/b.jpg", queued_sha)
        self.assertIn(queued_sha, ai.pending_shas)

        tripped_sha = "2" * 64
        ai.sniffed[tripped_sha] = b"image-bytes"
        for _ in range(ai.config["rateCeilingPerMinute"]):
            ai.breaker.record_start(time.monotonic())

        with patch("builtins.print"):
            await ai._process(None, "http://example.com/a.jpg", tripped_sha)

        self.assertTrue(ai.paused)
        # A trip must not drop queued work; the tripped candidate is re-queued.
        self.assertIn(queued_sha, ai.pending_shas)
        self.assertIn(tripped_sha, ai.pending_shas)
        # Resume continues where it left off.
        ai.paused = False
        candidate = await ai._next()
        self.assertIsNotNone(candidate)
        self.assertIn(candidate[2], {queued_sha, tripped_sha})

    async def test_attempts_stop_after_max_attempts_per_image(self):
        ai = self.make_ai()
        ai.config["retries"] = 3  # retries alone would allow more sends
        sha = "9" * 64
        ai.sniffed[sha] = b"image-bytes"
        calls = {"n": 0}

        def boom(data, src=None):
            calls["n"] += 1
            raise RuntimeError("network down")

        ai._post = boom
        for _ in range(ai.config["maxAttemptsPerImage"] + 3):
            await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertEqual(calls["n"], ai.config["maxAttemptsPerImage"])
        self.assertEqual(ai.attempt_counts.get(sha), ai.config["maxAttemptsPerImage"])
        self.assertNotIn(sha, ai.inflight)
        self.assertEqual(ai.counters["inflight"], 0)

    async def test_attempt_counter_increments_on_failed_sends(self):
        ai = self.make_ai()
        ai.config["retries"] = 0
        sha = "8" * 64
        ai.sniffed[sha] = b"image-bytes"

        def boom(data, src=None):
            raise RuntimeError("network down")

        ai._post = boom
        await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertEqual(ai.attempt_counts.get(sha), 1)
        self.assertEqual(ai.counters["failed"], 1)
        await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertEqual(ai.attempt_counts.get(sha), 2)
        self.assertEqual(ai.counters["failed"], 2)
        # The budget is spent: no more sends, no stray failure counts.
        await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertEqual(ai.attempt_counts.get(sha), 2)
        self.assertEqual(ai.counters["failed"], 2)

    async def test_reencounter_is_not_queued_after_cap(self):
        ai = self.make_ai()
        ai.config["retries"] = 5
        sha = "7" * 64
        url = "http://example.com/pic.jpg"
        ai.url_to_sha[url] = sha
        ai.sniffed[sha] = b"image-bytes"

        def boom(data, src=None):
            raise RuntimeError("network down")

        ai._post = boom
        page = _FakePage()
        for _ in range(ai.config["maxAttemptsPerImage"] + 2):
            await ai._process(page, url, sha)
        self.assertEqual(ai.attempt_counts.get(sha), ai.config["maxAttemptsPerImage"])

        ai.pending.clear()
        ai.pending_map.clear()
        ai.pending_shas.clear()
        report = {"type": "candidates", "items": [{"url": url, "w": 200, "h": 200, "order": 0}]}
        await ai._on_report(page, report)
        self.assertNotIn(sha, ai.pending_shas)
        self.assertEqual(ai.pending_map, {})

        # Below the cap the same image still queues normally.
        ai.attempt_counts[sha] = ai.config["maxAttemptsPerImage"] - 1
        await ai._on_report(page, report)
        self.assertIn(sha, ai.pending_shas)


class _FakeResponse:
    """Minimal requests.Response stand-in for the real _post/_post_url paths."""

    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}

    def json(self):
        return self._body


class QuotaProtectionTests(unittest.IsolatedAsyncioTestCase):
    """The real request layer is exercised with requests.post patched out."""

    def make_ai(self):
        ai = AICheck(browser_controller=None)
        ai.config = dict(DEFAULTS)
        ai.enabled = True
        ai.paused = False
        ai.closed = False
        ai.server = "http://127.0.0.1:1"
        ai.key = "test-key"
        return ai

    async def test_file_mode_post_carries_src(self):
        ai = self.make_ai()
        sha = "a1" * 32
        ai.sniffed[sha] = b"image-bytes"
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return _FakeResponse(200, {"score": 10, "source": "sightengine"})

        with patch("browserkit.ai_check.requests.post", fake_post):
            await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertEqual(captured["data"], {"src": "http://example.com/x.jpg"})
        self.assertIn("files", captured)
        self.assertEqual(ai.counters["scanned"], 1)

    async def test_url_mode_post_also_carries_src(self):
        ai = self.make_ai()
        sha = "b1" * 32  # no sniffed bytes: url mode
        captured = {}

        def fake_post(url, **kwargs):
            captured.update(kwargs)
            return _FakeResponse(200, {"score": 10, "source": "sightengine"})

        with patch("browserkit.ai_check.requests.post", fake_post):
            await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertEqual(
            captured["data"],
            {"url": "http://example.com/x.jpg", "src": "http://example.com/x.jpg"},
        )
        self.assertNotIn("files", captured)

    async def test_daily_budget_429_pauses_with_message(self):
        ai = self.make_ai()
        sha = "c1" * 32
        ai.sniffed[sha] = b"image-bytes"
        ai.pending_map[(id(None), "http://example.com/x.jpg")] = (-100, 0, 0)
        ai.pending_shas.add(sha)

        def fake_post(url, **kwargs):
            return _FakeResponse(429, {"error": "rate_limited", "reason": "daily_verdict_budget"})

        with patch("browserkit.ai_check.requests.post", fake_post), patch("builtins.print"):
            await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertTrue(ai.paused)
        self.assertIn("budget exhausted", ai.message)
        self.assertEqual(ai.pending_map, {})
        self.assertNotIn(sha, ai.inflight)
        self.assertEqual(ai.counters["inflight"], 0)
        self.assertEqual(ai.counters["budget"], 1)
        self.assertEqual(ai.counters["failed"], 1)

    async def test_per_image_429_is_terminal_not_breaker(self):
        ai = self.make_ai()
        sha = "d1" * 32
        ai.sniffed[sha] = b"image-bytes"

        def fake_post(url, **kwargs):
            return _FakeResponse(429, {"error": "rate_limited", "reason": "per_image_retry_budget"})

        with patch("browserkit.ai_check.requests.post", fake_post):
            await ai._process(None, "http://example.com/x.jpg", sha)
        self.assertEqual(ai.attempt_counts.get(sha), ai.config["maxAttemptsPerImage"])
        self.assertEqual(ai.counters["budget"], 1)
        # Per-image rejection must not trip the global breaker.
        self.assertFalse(ai.paused)
        self.assertTrue(ai.enabled)
        self.assertNotIn(sha, ai.pending_shas)
        self.assertEqual(ai.pending_map, {})
        self.assertNotIn(sha, ai.inflight)

    async def test_other_4xx_still_retries(self):
        ai = self.make_ai()
        sha = "e1" * 32
        ai.sniffed[sha] = b"image-bytes"
        calls = {"n": 0}

        def fake_post(url, **kwargs):
            calls["n"] += 1
            return _FakeResponse(500, {})

        with patch("browserkit.ai_check.requests.post", fake_post):
            await ai._process(None, "http://example.com/x.jpg", sha)
        # A generic failure still requeues for a retry (retries default 1).
        self.assertEqual(calls["n"], 1)
        self.assertEqual(ai.counters["failed"], 0)
        self.assertEqual(ai.counters["budget"], 0)
        self.assertIn(sha, ai.pending_shas)
        self.assertIn((id(None), "http://example.com/x.jpg"), ai.pending_map)
        self.assertNotIn(sha, ai.inflight)


if __name__ == "__main__":
    unittest.main()
