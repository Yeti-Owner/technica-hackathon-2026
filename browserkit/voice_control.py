"""Bridge the single local microphone to the focused browser document."""
import asyncio
import queue
import time

import numpy as np
from playwright.async_api import Error

from .voice import Microphone, VowelDetector
from .controller import ROOT


class VoiceControl:
    def __init__(self, browser):
        self.browser = browser
        self.detector = VowelDetector(ROOT / "artifacts/voice-profile.json")
        self.mic = None
        self.enabled = False
        self.message = "Mic off"
        self.task = None
        self.audio_task = None
        self.device = None
        self.device_label = ''
        self.target = None
        self.window = np.zeros(0, dtype=np.float32)
        self.last_audio = 0.
        self.sessions = {}

    def start(self):
        try:
            self.pause()
            self.device_label = ''
            self.mic = Microphone()
            self.mic.start(self.device)
            self.device_label = f'{self.mic.name} (input {self.mic.device})'
            self.enabled = True
            self.message = "Listening · ooo ↓ / aaa ↑"
            print(f"Microphone checked: {self.device_label}; audio arriving at {self.mic.rate} Hz.")
            print("Microphone on: hold ooo to scroll down, aaa to scroll up. Pause between directions. 'mic off' pauses.")
        except Exception as error:
            self.enabled = False
            self.message = "Mic unavailable · see terminal"
            print(f"Microphone unavailable: {error}. Check your default input and OS microphone access, then type 'mic on'.")
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())
        elif self.audio_task is not None and self.audio_task.done():
            self.audio_task = asyncio.create_task(self._audio_loop())

    def select_device(self, device):
        self.device = device
        self.start()

    def status(self):
        return f'{self.message} · {self.device_label or "No microphone open"} · {self.detector.reason}'

    def pause(self):
        self.enabled = False
        if self.mic:
            self.mic.close()
        self.detector.reset()
        self.window = np.zeros(0, dtype=np.float32)
        self.message = "Mic off · type mic on to resume"

    def reset_learning(self):
        self.detector = VowelDetector()
        self.detector.profile = ROOT / "artifacts/voice-profile.json"
        self.detector.save()
        print("Voice learning reset to defaults.")

    async def _page_tick(self, page, speed, message):
        try:
            if page not in self.sessions:
                session = await self.browser.context.new_cdp_session(page)
                # Playwright otherwise makes every tab report focused/visible.
                await session.send('Emulation.setFocusEmulationEnabled', {'enabled': False})
                self.sessions[page] = session
            return await asyncio.wait_for(page.evaluate("""args => {
                if (!window.__voiceTick) return false;
                return window.__voiceTick(args);
            }""", {"speed": speed, "message": message}), timeout=.2)
        except asyncio.TimeoutError:
            return None  # Unknown focus is not evidence that the user switched tabs.
        except Error:
            return False if page.is_closed() else None

    async def run(self):
        last_save = time.monotonic()
        self.audio_task = asyncio.create_task(self._audio_loop())
        try:
            while self.browser.browser.is_connected():
                now = time.monotonic()
                strength = self.detector.strength(now)
                direction = self.detector.direction if self.enabled else 0
                message = self.message
                if direction:
                    message = "Scrolling down · ooo ↓" if direction == 1 else "Scrolling up · aaa ↑"
                if self.device_label:
                    message += ' · ' + self.device_label
                pages = list(self.browser.context.pages)
                for closed in list(self.sessions):
                    if closed not in pages:
                        del self.sessions[closed]
                # Each document checks focus again before scrolling. No page bindings
                # can invoke Python or request access to the microphone.
                results = await asyncio.gather(*(self._page_tick(
                    page, direction*240*strength if page == self.target else 0, message)
                    for page in pages))
                focused = next((p for p, active in zip(pages, results) if active), None)
                if (focused is None and self.target in pages
                        and results[pages.index(self.target)] is None):
                    # Preserve the gesture through a transient evaluation failure.
                    # The document still rechecks real focus before every scroll.
                    focused = self.target
                if focused != self.target:
                    self.detector.reset()
                    self.target = focused
                if focused is None:
                    self.detector.reset()
                else:
                    self.browser.page = focused
                if now-last_save > 30:
                    self._save()
                    last_save = now
                await asyncio.sleep(.025)
        except Exception as error:
            self.pause()
            print(f"Voice scrolling stopped: {error}. Type 'mic on' to retry.")
        finally:
            self.audio_task.cancel()
            try:
                await self.audio_task
            except asyncio.CancelledError:
                pass

    async def _audio_loop(self):
        try:
            while True:
                now = time.monotonic()
                if self.enabled and self.mic:
                    while True:
                        try:
                            stamp, block, overflow = self.mic.blocks.get_nowait()
                        except queue.Empty:
                            break
                        if now-stamp > .15:
                            self.window = np.zeros(0, dtype=np.float32)
                            continue
                        if overflow or stamp-self.last_audio > .09:
                            self.window = np.zeros(0, dtype=np.float32)
                        self.last_audio = stamp
                        self.window = np.concatenate((self.window, block))[-int(self.mic.rate*.06):]
                        if len(self.window) >= int(self.mic.rate*.06):
                            self.detector.update(self.window, self.mic.rate, stamp)
                    if now-self.last_audio > .09:
                        self.detector.expire(now)
                await asyncio.sleep(.01)
        except Exception as error:
            self.pause()
            print(f'Audio processing stopped: {error}. Restart the browser to retry.')

    def _save(self):
        try:
            self.detector.save()
        except OSError as error:
            print(f"Could not save voice preferences: {error}")

    async def close(self):
        self.pause()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self._save()
