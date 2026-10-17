"""Offline integration check for the passive AI image checker."""
import asyncio
import base64
import hashlib
import json
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from browserkit import BrowserController
from browserkit.ai_check import AICheck

PNGS = {
    "ai-a.png": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC",
    "ai-b.png": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNg+M8AAAICAQB7CYF4AAAAAElFTkSuQmCC",
    "ai-e.png": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z/AfAAQAAf8iCjrwAAAAAElFTkSuQmCC",
    "photo-c.png": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYPgPAAEDAQAIicLsAAAAAElFTkSuQmCC",
    "ai-d.png": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4/58BAAT/Af9dfQKHAAAAAElFTkSuQmCC",
}
IMAGES = {name: base64.b64decode(data) for name, data in PNGS.items()}


class StubHandler(BaseHTTPRequestHandler):
    checks = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if not path.startswith("/img/"):
            self.send_error(404)
            return
        data = IMAGES.get(path[5:])
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if urlsplit(self.path).path != "/check":
            self.send_error(404)
            return
        if self.headers.get("X-API-Key") != "smoke-key":
            self.send_response(401)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        StubHandler.checks += 1
        score, source = 50, "inconclusive"
        for name, data in IMAGES.items():
            if data in body:
                score = 92 if name.startswith("ai-") else 10
                source = "smoke"
                break
        payload = json.dumps({
            "id": hashlib.sha256(body).hexdigest(),
            "score": score,
            "source": source,
            "cached": False,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def fixture(port, whitelisted=False):
    if whitelisted:
        return f"""<!doctype html><html><head><meta charset="utf-8"><style>
img {{ width:320px; height:320px; display:block; }}
</style></head><body>
<img id="w" src="http://127.0.0.1:{port}/img/ai-a.png?whitelist=1">
</body></html>"""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
img {{ display:block; }}
#a, #b, #e, #c {{ width:320px; height:320px; }}
#d {{ width:40px; height:40px; }}
</style></head><body>
<img id="a" src="http://127.0.0.1:{port}/img/ai-a.png">
<img id="b" src="http://127.0.0.1:{port}/img/ai-b.png">
<img id="e" src="http://127.0.0.1:{port}/img/ai-e.png">
<img id="c" src="http://127.0.0.1:{port}/img/photo-c.png">
<img id="d" src="http://127.0.0.1:{port}/img/ai-d.png">
</body></html>"""


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["AI_CHECK_SERVER_URL"] = f"http://127.0.0.1:{port}"
    os.environ["AI_CHECK_API_KEY"] = "smoke-key"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        page_path = root / "fixture.html"
        page_path.write_text(fixture(port), encoding="utf-8")
        whitelist_path = root / "whitelist.html"
        whitelist_path.write_text(fixture(port, whitelisted=True), encoding="utf-8")
        config_path = root / "ai-check.json"
        config_path.write_text(json.dumps({
            "threshold": 70,
            "minSize": 80,
            "prefetchScreens": 1.5,
            "concurrency": 2,
            "failStreakLimit": 5,
            "rateCeilingPerMinute": 30,
            "requestTimeoutSeconds": 35,
            "maxBodyBytes": 15728640,
            "whitelist": [whitelist_path.as_uri()],
        }), encoding="utf-8")
        async with BrowserController() as browser:
            ai = AICheck(browser, config_path=config_path)
            ai.start()
            try:
                await asyncio.sleep(.2)
                await browser.goto(page_path.as_uri())
                await browser.page.wait_for_load_state("load")
                deadline = time.monotonic() + 15
                verdicts = {}
                while time.monotonic() < deadline:
                    verdicts = await browser.page.evaluate("""() => Object.fromEntries(
                        Array.from(document.querySelectorAll('img')).map(img => [img.id, img.dataset.aiVerdict || null]))""")
                    if verdicts.get("a") and verdicts.get("b") and verdicts.get("e"):
                        break
                    await asyncio.sleep(.1)
                assert verdicts.get("a") and verdicts.get("b") and verdicts.get("e"), verdicts
                assert not verdicts.get("c"), verdicts
                assert not verdicts.get("d"), verdicts
                outlines = await browser.page.evaluate("""() => Object.fromEntries(
                    Array.from(document.querySelectorAll('img')).map(img => {
                        const style = getComputedStyle(img);
                        return [img.id, style.outlineColor + ' ' + style.outlineWidth];
                    }))""")
                for key in ("a", "b", "e"):
                    assert "255, 23, 68" in outlines[key] or "#ff1744" in outlines[key], outlines
                    assert "6px" in outlines[key], outlines
                status = ai.status()
                marked = int(status.split("marked ")[1].split(" ")[0])
                assert marked >= 3, status
                before = StubHandler.checks
                await browser.goto(whitelist_path.as_uri())
                await browser.page.wait_for_load_state("load")
                await asyncio.sleep(2)
                after = StubHandler.checks
                assert after == before, (before, after)
            finally:
                await ai.close()
    server.shutdown()
    server.server_close()
    print("PASS: passive sniffing, AI verdicts, red marks, min-size skip, and whitelist isolation")


if __name__ == "__main__":
    asyncio.run(main())
