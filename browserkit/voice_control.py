"""Local one-shot vowel clicks on the element selected by keyboard/head focus."""
import asyncio
import queue
import time

import numpy as np
from playwright.async_api import Error

from .voice import ClickDetector, Microphone, VowelDetector
from .controller import ROOT


class VoiceControl:
    def __init__(self, browser):
        self.browser = browser
        self.detector = VowelDetector(ROOT / 'artifacts/voice-profile.json')
        self.gesture = ClickDetector(self.detector)
        self.mic = None
        self.enabled = False
        self.controls_enabled = True
        self._controls_generation = 0
        self._dispatch_task = None
        self.message = 'Mic off'
        self.task = self.audio_task = None
        self.device = None
        self.device_label = ''
        self.target = self.candidate_target = self.pending = None
        self.window = np.zeros(0, dtype=np.float32)
        self.last_audio = 0.
        self.sessions = {}

    def start(self):
        self.pause()
        self.device_label = ''
        try:
            self.mic = Microphone()
            self.mic.start(self.device)
            self.detector.reset_environment()
            self.device_label = f'{self.mic.name} (input {self.mic.device})'
            self.enabled = True
            self.message = 'Listening: ooo left-click / aaa right-click'
            print(f'Microphone checked: {self.device_label}.')
            print('Focus an item with your head or Tab. Pause, then hold ooo or aaa steadily for about half a second. One click per sound; pause between clicks.')
        except Exception as error:
            self.message = 'Mic unavailable'
            print(f'Microphone unavailable: {error}. Check your input, then use mic on.')
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())
        elif self.audio_task is not None and self.audio_task.done():
            self.audio_task = asyncio.create_task(self._audio_loop())

    def select_device(self, device):
        self.device = device
        self.start()

    def status(self):
        gate = 'Voice controls on' if self.controls_enabled else 'Voice controls paused'
        return f'{gate} · {self.message} · {self.device_label or "No microphone open"} · {self.gesture.reason}'

    def set_controls_enabled(self, enabled):
        self.controls_enabled = bool(enabled)
        self._controls_generation += 1
        if self._dispatch_task:
            self._dispatch_task.cancel()
        # Also guard a click that is already waiting for Playwright's focus lock.
        self.browser.voice_controls_enabled = self.controls_enabled
        self.browser.voice_control_generation = getattr(self.browser, 'voice_control_generation', 0) + 1
        self._cancel()
        self.window = np.zeros(0, dtype=np.float32)
        self.last_audio = 0.
        if self.mic:
            while True:
                try:
                    self.mic.blocks.get_nowait()
                except queue.Empty:
                    break
        print(f'[controls] Voice controls {"ON" if self.controls_enabled else "OFF"}', flush=True)

    def _cancel(self, reason='voice controls paused or reset', preserve_pending=False):
        self.gesture.cancel()
        self.candidate_target = None
        if not preserve_pending:
            if self.pending:
                print(f'Voice click skipped: {reason}.', flush=True)
            self.pending = None

    async def _dispatch_pending(self, target):
        if not self.pending:
            return
        stamp, action, intended = self.pending
        self.pending = None  # Consume before dispatch: never retry a click.
        if not self.enabled or not self.controls_enabled or target != intended:
            print('Voice click skipped: focus changed or microphone paused.', flush=True)
            return
        if time.monotonic()-stamp > .75:
            print('Voice click skipped: browser response took too long.', flush=True)
            return
        generation = self._controls_generation
        dispatch = asyncio.create_task(self.browser.click_focused(target, 'left' if action == 1 else 'right'))
        self._dispatch_task = dispatch
        try:
            clicked = await dispatch
            if clicked:
                print('Voice left click sent' if action == 1 else 'Voice right click sent', flush=True)
            else:
                print('Voice click skipped: selected item or browser lost focus.', flush=True)
        except asyncio.CancelledError:
            if generation == self._controls_generation:
                raise
            print('Voice click skipped: voice controls toggled.', flush=True)
        except Error:
            print('Voice click skipped: selected item moved, is covered, or is unavailable.', flush=True)
        finally:
            if self._dispatch_task is dispatch:
                self._dispatch_task = None

    def pause(self):
        self.enabled = False
        if self.mic:
            self.mic.close()
        self._cancel()
        self.window = np.zeros(0, dtype=np.float32)
        self.message = 'Mic off · type mic on to resume'

    def reset_learning(self):
        self.detector = VowelDetector()
        self.detector.profile = ROOT / 'artifacts/voice-profile.json'
        self.gesture = ClickDetector(self.detector)
        self._cancel()
        self.detector.save()
        print('Voice learning reset to defaults.')

    async def _focused_target(self):
        pages = list(self.browser.context.pages)
        for closed in list(self.sessions):
            if closed not in pages:
                del self.sessions[closed]
        for page in pages:
            if page not in self.sessions:
                session = await self.browser.context.new_cdp_session(page)
                await session.send('Emulation.setFocusEmulationEnabled', {'enabled': False})
                self.sessions[page] = session
            if not await page.evaluate('document.visibilityState === "visible" && document.hasFocus()'):
                continue
            self.browser.page = page  # Head navigation follows the actual active tab.
            for frame in page.frames:
                state = await frame.evaluate('''() => {
                    const s = window.__voiceFocus?.();
                    return s?.active && s.element ? {key: s.key} : null;
                }''')
                if state:
                    return page, frame, state['key']
        return None

    async def run(self):
        last_save = time.monotonic()
        self.audio_task = asyncio.create_task(self._audio_loop())
        try:
            while self.browser.browser.is_connected():
                try:
                    target = await asyncio.wait_for(self._focused_target(), timeout=.25)
                except (Error, asyncio.TimeoutError):
                    target = None
                if target != self.target or target is None:
                    self._cancel('focus changed or no clickable item is selected')
                self.target = target
                await self._dispatch_pending(target)
                if time.monotonic()-last_save > 30:
                    self._save()
                    last_save = time.monotonic()
                await asyncio.sleep(.04)
        except Exception as error:
            self.pause()
            print(f'Voice clicks stopped: {error}. Type mic on to retry.')
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
                if self.enabled and self.controls_enabled and self.mic:
                    while True:
                        try:
                            stamp, block, overflow = self.mic.blocks.get_nowait()
                        except queue.Empty:
                            break
                        if now-stamp > .15 or overflow or stamp-self.last_audio > .12:
                            self._cancel(preserve_pending=True)
                            self.window = np.zeros(0, dtype=np.float32)
                        self.last_audio = stamp
                        if now-stamp > .15 or overflow:
                            continue
                        self.window = np.concatenate((self.window, block))[-int(self.mic.rate*.06):]
                        if len(self.window) < int(self.mic.rate*.06):
                            continue
                        was_candidate = self.gesture.candidate
                        action = self.gesture.update(self.window, self.mic.rate, stamp)
                        if action:
                            print('Detected "ooo"' if action == 1 else 'Detected "aaa"', flush=True)
                        if self.gesture.candidate and not was_candidate:
                            self.candidate_target = self.target
                        if action and self.target and self.candidate_target == self.target:
                            self.pending = (stamp, action, self.candidate_target)
                        elif action:
                            print('Voice click skipped: focus an item with Tab or head controls first.', flush=True)
                        if not self.target:
                            self._cancel()
                    if now-self.last_audio > .12:
                        # An audio gap cancels an unfinished sound, not a click
                        # already confirmed and waiting for browser validation.
                        self._cancel(preserve_pending=True)
                elif self.mic:
                    # Keep the capture stream live, but never classify or replay paused audio.
                    while True:
                        try:
                            self.mic.blocks.get_nowait()
                        except queue.Empty:
                            break
                await asyncio.sleep(.01)
        except Exception as error:
            self.pause()
            print(f'Audio processing stopped: {error}. Type mic on to retry.')

    def _save(self):
        try:
            self.detector.save()
        except OSError as error:
            print(f'Could not save voice preferences: {error}')

    async def close(self):
        self.pause()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self._save()
