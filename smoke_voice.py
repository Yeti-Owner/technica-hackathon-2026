"""Dormancy guard: the voice/humming stack is disabled by design.

The full scrolling smoke lives in git history; it needs ui/voice.js injected and
VoiceControl.start() enabled. This guard instead asserts the current decision:
start() is a no-op and voice.js is not injected into pages.
"""
import asyncio

from browserkit import BrowserController
from browserkit.controller import ROOT
from browserkit.voice_control import VoiceControl


async def main():
    async with BrowserController() as browser:
        await browser.goto((ROOT / 'ui/practice.html').as_uri())
        page = browser.page

        voice = VoiceControl(browser)
        voice.start()
        assert voice.enabled is False, 'voice stack unexpectedly enabled'
        assert voice.task is None, 'voice loop unexpectedly started'
        assert voice.mic is None, 'microphone unexpectedly opened'

        assert await page.evaluate('window.__voiceTick === undefined'), 'voice.js was injected'
        assert await page.evaluate(
            "document.querySelector('#browser-voice-status') === null"
        ), 'voice status badge present'

    print('PASS: voice stack dormant by design (start() no-op, voice.js not injected); '
          'full scrolling smoke reserved for future use (see git history)')


if __name__ == '__main__':
    asyncio.run(main())
