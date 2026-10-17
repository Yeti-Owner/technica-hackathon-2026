"""Passive AI-image checking: sniff image responses, ask the server, mark verdicts."""
import asyncio
import hashlib
import heapq
import json
import os
import time
from collections import OrderedDict, deque
from pathlib import Path

import requests
from playwright.async_api import Error

from .controller import ROOT

DEFAULTS = {
    "threshold": 70,
    "minSize": 80,
    "prefetchScreens": 1.5,
    "concurrency": 2,
    "failStreakLimit": 5,
    "rateCeilingPerMinute": 30,
    "requestTimeoutSeconds": 35,
    "maxBodyBytes": 15728640,
    "whitelist": [],
}

SNIFF_BYTES = 32 * 1024 * 1024
SNIFF_ENTRIES = 64
URL_ENTRIES = 4096


def read_env(path):
    """Parse a simple KEY = VALUE file; blank lines and # comments are ignored."""
    values = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value.strip()
    return values


def secret(name, values):
    """Environment variables win over the local .env file."""
    if name in os.environ:
        return os.environ[name].strip()
    return values.get(name, "").strip()


def is_configured(server, key):
    if not server or not key:
        return False
    if not server.startswith("http://"):
        return False
    return "PUT " not in server and "PUT " not in key


def _int(value, default, low=None, high=None):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    if low is not None and value < low:
        return default
    if high is not None and value > high:
        return default
    return value


def _float(value, default, low=None):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    if low is not None and value < low:
        return default
    return value


def validate_config(raw):
    """Keep known keys, ignore unknown ones, and fall back on bad values."""
    config = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return config
    config["threshold"] = _int(raw.get("threshold"), DEFAULTS["threshold"], 0, 100)
    config["minSize"] = _int(raw.get("minSize"), DEFAULTS["minSize"], 0)
    config["prefetchScreens"] = _float(raw.get("prefetchScreens"), DEFAULTS["prefetchScreens"], 0)
    config["concurrency"] = _int(raw.get("concurrency"), DEFAULTS["concurrency"], 1, 3)
    config["failStreakLimit"] = _int(raw.get("failStreakLimit"), DEFAULTS["failStreakLimit"], 1)
    config["rateCeilingPerMinute"] = _int(raw.get("rateCeilingPerMinute"), DEFAULTS["rateCeilingPerMinute"], 1)
    config["requestTimeoutSeconds"] = _float(raw.get("requestTimeoutSeconds"), DEFAULTS["requestTimeoutSeconds"], 1)
    config["maxBodyBytes"] = _int(raw.get("maxBodyBytes"), DEFAULTS["maxBodyBytes"], 1)
    whitelist = raw.get("whitelist")
    if isinstance(whitelist, list):
        config["whitelist"] = [item for item in whitelist if isinstance(item, str) and item]
    return config


def load_config(path):
    """Return (config, error). A broken file idles the feature with one message."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(DEFAULTS), None
    except (OSError, ValueError) as error:
        return dict(DEFAULTS), f"ai-check.json unreadable: {error}"
    if not isinstance(raw, dict):
        return dict(DEFAULTS), "ai-check.json must contain an object"
    return validate_config(raw), None


def builtin_whitelist():
    """Controller-owned pages never need checking."""
    return [(ROOT / "ui/home.html").as_uri(), (ROOT / "ui/practice.html").as_uri()]


def is_whitelisted(url, whitelist, builtin=()):
    if not url:
        return False
    for prefix in list(whitelist or []) + list(builtin):
        if prefix and url.startswith(prefix):
            return True
    return False


def passes_min_size(width, height, min_size):
    return width >= min_size and height >= min_size


def priority_key(area, order, seq):
    """Largest area first, then earliest document order, then insertion order."""
    return (-area, order, seq)


def should_mark(score, source, threshold):
    if source == "inconclusive":
        return False
    try:
        score = int(score)
    except (TypeError, ValueError):
        return False
    return score >= threshold


def too_large(size, max_bytes):
    return size > max_bytes


class RunawayBreaker:
    """Consecutive failures or a burst of starts pause the feature."""

    def __init__(self, fail_limit, rate_limit, window=60.0):
        self.fail_limit = fail_limit
        self.rate_limit = rate_limit
        self.window = window
        self.failures = 0
        self.starts = deque()

    def success(self):
        self.failures = 0

    def failure(self):
        self.failures += 1
        return self.failures >= self.fail_limit

    def _prune(self, now):
        while self.starts and now - self.starts[0] > self.window:
            self.starts.popleft()

    def record_start(self, now):
        self._prune(now)
        self.starts.append(now)

    def rate_exceeded(self, now):
        self._prune(now)
        return len(self.starts) >= self.rate_limit

    def reset(self):
        self.failures = 0
        self.starts.clear()


class AICheck:
    def __init__(self, browser_controller, config_path=None):
        self.browser = browser_controller
        self.config_path = Path(config_path) if config_path else ROOT / "ai-check.json"
        self.config = dict(DEFAULTS)
        self.builtin = builtin_whitelist()
        self.server = ""
        self.key = ""
        self.enabled = False
        self.paused = True
        self.closed = False
        self.message = "not started"
        self.last_error = None
        self.counters = {"scanned": 0, "marked": 0, "failed": 0, "inflight": 0}
        self.lock = asyncio.Lock()
        self.pending = []
        self.pending_map = {}
        self.pending_shas = set()
        self.seq = 0
        self.inflight = set()
        self.sniffed = OrderedDict()
        self.sniffed_bytes = 0
        self.url_to_sha = OrderedDict()
        self.workers = []
        self.wired = set()
        self.breaker = RunawayBreaker(DEFAULTS["failStreakLimit"], DEFAULTS["rateCeilingPerMinute"])
        self.wake = asyncio.Event()

    def start(self):
        self.config, error = load_config(self.config_path)
        if error:
            self.enabled = False
            self.message = error
            print(f"ai: {error}; feature idle.")
            return
        values = read_env(ROOT / ".env")
        server = secret("AI_CHECK_SERVER_URL", values)
        key = secret("AI_CHECK_API_KEY", values)
        if not is_configured(server, key):
            self.enabled = False
            self.message = "missing AI_CHECK_SERVER_URL / AI_CHECK_API_KEY in .env"
            print("AI image check idle: fill AI_CHECK_SERVER_URL and AI_CHECK_API_KEY in .env, then restart.")
            return
        self.server = server.rstrip("/")
        self.key = key
        self.enabled = True
        self.paused = False
        self.message = "AI check on"
        self.breaker = RunawayBreaker(self.config["failStreakLimit"], self.config["rateCeilingPerMinute"])
        self._start_workers()
        self.browser.context.on("response", self._on_response)
        self.browser.context.on("page", self._on_page)
        for page in list(self.browser.context.pages):
            asyncio.create_task(self._wire_page(page))

    def status(self):
        if not self.enabled:
            text = f"AI check off · {self.message}"
        elif self.paused:
            text = f"AI check off · {self.message}"
        else:
            counters = self.counters
            text = (f"AI check on · scanned {counters['scanned']} · marked {counters['marked']}"
                    f" · queued {len(self.pending_map)} · inflight {counters['inflight']}"
                    f" · past errors {counters['failed']}")
        if self.last_error:
            text += f" · last error: {self.last_error}"
        return text

    def pause(self):
        self.paused = True
        self.message = "type 'ai on' to resume"
        self._clear_pending()
        self.wake.set()

    def resume(self):
        if not self.enabled:
            print("AI image check is not configured; fill .env and restart.")
            return
        self.paused = False
        self.message = "AI check on"
        self.last_error = None
        self.breaker.reset()
        self._start_workers()
        for page in list(self.browser.context.pages):
            asyncio.create_task(self._push_config(page))

    def set_concurrency(self, value):
        value = int(value)
        if value not in (1, 2, 3):
            raise ValueError("concurrency must be 1, 2 or 3")
        self.config["concurrency"] = value
        if not self.enabled or self.paused:
            return
        self.workers = [task for task in self.workers if not task.done()]
        while len(self.workers) < value:
            self.workers.append(asyncio.create_task(self._worker()))
        while len(self.workers) > value:
            self.workers.pop().cancel()

    async def close(self):
        self.closed = True
        self.enabled = False
        self.paused = True
        self._clear_pending()
        self.wake.set()
        for task in self.workers:
            task.cancel()
        for task in self.workers:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self.workers = []
        try:
            self.browser.context.remove_listener("response", self._on_response)
        except Exception:
            pass
        try:
            self.browser.context.remove_listener("page", self._on_page)
        except Exception:
            pass

    def is_whitelisted(self, url):
        return is_whitelisted(url, self.config.get("whitelist", []), self.builtin)

    def _start_workers(self):
        self.workers = [task for task in self.workers if not task.done()]
        while len(self.workers) < self.config["concurrency"]:
            self.workers.append(asyncio.create_task(self._worker()))

    def _clear_pending(self):
        self.pending.clear()
        self.pending_map.clear()
        self.pending_shas.clear()

    def _on_page(self, page):
        if self.closed:
            return
        asyncio.create_task(self._wire_page(page))

    async def _wire_page(self, page):
        if page in self.wired or self.closed:
            return
        self.wired.add(page)
        try:
            await page.expose_function("pyAIReport", lambda payload: self._on_report(page, payload))
        except Exception as error:
            self.last_error = str(error)
            return
        page.on("load", lambda: asyncio.create_task(self._push_config(page)))
        page.on("framenavigated", lambda frame: asyncio.create_task(self._push_config(page)))
        page.on("close", lambda: self.wired.discard(page))
        await self._push_config(page)

    async def _push_config(self, page):
        try:
            if page.is_closed():
                return
            payload = {
                "enabled": not self.is_whitelisted(page.url),
                "minSize": int(self.config["minSize"]),
                "prefetchScreens": float(self.config["prefetchScreens"]),
            }
            for frame in page.frames:
                try:
                    await frame.evaluate("(cfg) => window.__aiConfigure && window.__aiConfigure(cfg)", payload)
                except Error:
                    pass
        except Exception as error:
            self.last_error = str(error)

    async def _on_response(self, response):
        try:
            if not self.enabled or self.paused or self.closed:
                return
            headers = response.headers or {}
            if not headers.get("content-type", "").lower().startswith("image/"):
                return
            length = headers.get("content-length")
            if length and length.isdigit() and too_large(int(length), self.config["maxBodyBytes"]):
                return
            try:
                frame = response.frame
                page = frame.page if frame else None
            except Error:
                page = None
            if page is not None and self.is_whitelisted(page.url):
                return
            try:
                data = await response.body()
            except Exception:
                return
            if not data or too_large(len(data), self.config["maxBodyBytes"]):
                return
            sha = hashlib.sha256(data).hexdigest()
            urls = {response.url}
            try:
                request = response.request
                while request is not None:
                    urls.add(request.url)
                    request = request.redirected_from
            except Error:
                pass
            async with self.lock:
                self._remember(sha, data, urls)
        except Exception as error:
            self.last_error = str(error)

    def _remember(self, sha, data, urls):
        if sha in self.sniffed:
            self.sniffed.move_to_end(sha)
        else:
            self.sniffed[sha] = data
            self.sniffed_bytes += len(data)
            while len(self.sniffed) > SNIFF_ENTRIES or self.sniffed_bytes > SNIFF_BYTES:
                _, old = self.sniffed.popitem(last=False)
                self.sniffed_bytes -= len(old)
        for url in urls:
            self.url_to_sha[url] = sha
            self.url_to_sha.move_to_end(url)
        while len(self.url_to_sha) > URL_ENTRIES:
            self.url_to_sha.popitem(last=False)

    async def _on_report(self, page, payload):
        try:
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                return
            if self.is_whitelisted(page.url):
                return
            kind = payload.get("type")
            if kind == "needConfig":
                await self._push_config(page)
                return
            if kind != "candidates" or not self.enabled or self.paused:
                return
            items = payload.get("items")
            if not isinstance(items, list):
                return
            async with self.lock:
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    url = item.get("url")
                    if not isinstance(url, str) or not url:
                        continue
                    try:
                        width = float(item.get("w") or 0)
                        height = float(item.get("h") or 0)
                        order = int(item.get("order") or 0)
                    except (TypeError, ValueError):
                        continue
                    if not passes_min_size(width, height, self.config["minSize"]):
                        continue
                    sha = self.url_to_sha.get(url)
                    if not sha or sha not in self.sniffed:
                        continue
                    if sha in self.inflight:
                        continue
                    area = width * height
                    key = (id(page), url)
                    old = self.pending_map.get(key)
                    if old is not None:
                        if old[0] <= -area:
                            continue
                        seq = self.seq
                        self.seq += 1
                        heapq.heappush(self.pending, (-area, order, seq, page, url, sha))
                        self.pending_map[key] = (-area, order, seq)
                        continue
                    if sha in self.pending_shas:
                        continue
                    seq = self.seq
                    self.seq += 1
                    heapq.heappush(self.pending, (-area, order, seq, page, url, sha))
                    self.pending_map[key] = (-area, order, seq)
                    self.pending_shas.add(sha)
            self.wake.set()
        except Exception as error:
            self.last_error = str(error)

    async def _next(self):
        async with self.lock:
            while self.pending:
                neg_area, order, seq, page, url, sha = heapq.heappop(self.pending)
                key = (id(page), url)
                if self.pending_map.get(key) != (neg_area, order, seq):
                    continue
                del self.pending_map[key]
                self.pending_shas.discard(sha)
                if sha in self.inflight:
                    continue
                return page, url, sha
        return None

    async def _worker(self):
        try:
            while self.enabled and not self.paused and not self.closed:
                candidate = await self._next()
                if candidate is None:
                    self.wake.clear()
                    try:
                        await asyncio.wait_for(self.wake.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
                    continue
                await self._process(*candidate)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self.last_error = str(error)

    async def _process(self, page, url, sha):
        async with self.lock:
            if sha in self.inflight:
                return
            self.inflight.add(sha)
            self.counters["inflight"] += 1
            now = time.monotonic()
            if self.paused or self.closed:
                self.inflight.discard(sha)
                self.counters["inflight"] -= 1
                return
            if self.breaker.rate_exceeded(now):
                self.inflight.discard(sha)
                self.counters["inflight"] -= 1
                trip = True
            else:
                self.breaker.record_start(now)
                trip = False
                data = self.sniffed.get(sha)
        if trip:
            self._trip()
            return
        try:
            if data is None:
                return
            try:
                result = await asyncio.to_thread(self._post, data)
            except Exception as error:
                async with self.lock:
                    self.counters["failed"] += 1
                    self.last_error = str(error)
                    tripped = self.breaker.failure()
                if tripped:
                    self._trip()
                return
            async with self.lock:
                self.counters["scanned"] += 1
                self.breaker.success()
            if should_mark(result.get("score"), result.get("source"), self.config["threshold"]):
                await self._apply(page, url, result.get("score"))
                async with self.lock:
                    self.counters["marked"] += 1
        finally:
            self.inflight.discard(sha)
            self.counters["inflight"] = max(0, self.counters["inflight"] - 1)

    def _post(self, data):
        response = requests.post(
            f"{self.server}/check",
            headers={"X-API-Key": self.key},
            files={"file": ("image", data)},
            timeout=self.config["requestTimeoutSeconds"],
        )
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("response is not an object")
        return result

    async def _apply(self, page, url, score):
        try:
            if page.is_closed():
                return
            for frame in page.frames:
                try:
                    await frame.evaluate(
                        "([u, s]) => { if (window.__aiApply) window.__aiApply(u, s); }",
                        [url, score],
                    )
                except Error:
                    pass
        except Exception as error:
            self.last_error = str(error)

    def _trip(self):
        if self.paused or self.closed:
            return
        self.paused = True
        self.message = "suspected runaway loop, type 'ai on' to resume"
        self._clear_pending()
        self.wake.set()
        print("ai: paused — suspected runaway loop, type 'ai on' to resume")
