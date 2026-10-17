"""Visible browser integration check with generated audio, never live microphone audio."""
import asyncio
import queue
import time
from types import SimpleNamespace

from browserkit import BrowserController
from browserkit.controller import ROOT
from browserkit.voice_control import VoiceControl
from browserkit.voice import VowelDetector
from test_voice import vowel


async def main():
    async with BrowserController() as browser:
        # Keep this local fixture deterministic when the user switches OS windows.
        # Simulate focus per page; this tests routing, not native window activation.
        await browser.context.add_init_script("""
            window.__testFocus = false;
            Object.defineProperty(document, 'hasFocus', {value: () => window.__testFocus});
        """)
        await browser.goto((ROOT / 'ui/practice.html').as_uri())
        page = browser.page
        await page.locator('h1').click()
        await page.evaluate('window.__testFocus = true')
        voice = VoiceControl(browser)
        voice.detector = VowelDetector()
        voice.mic = SimpleNamespace(rate=48000, blocks=queue.Queue(), close=lambda: None)
        voice.enabled = True
        voice.message = 'Listening · ooo down / aaa up'
        voice.task = asyncio.create_task(voice.run())

        async def feed(duration, require_hold=False):
            block = vowel([350, 850, 2500])[-1440:]
            end = time.monotonic()+duration
            while time.monotonic() < end:
                voice.mic.blocks.put((time.monotonic(), block, False))
                await asyncio.sleep(.03)
                if require_hold:
                    assert voice.detector.direction == 1, {
                        'error': 'Browser delay interrupted audio detection',
                        'reason': voice.detector.reason, 'target': voice.target == page,
                        'focused': await page.evaluate('document.hasFocus()'),
                        'audio_age': time.monotonic()-voice.last_audio}

        try:
            await feed(1.3)
            first_y = await page.evaluate('scrollY')
            assert first_y > 20, first_y
            original_tick = voice._page_tick
            async def slow_tick(*args):
                await asyncio.sleep(.22)
                await original_tick(*args)
                return None  # Also exercise unknown focus during a page timeout.
            voice._page_tick = slow_tick
            await feed(.8, require_hold=True)
            voice._page_tick = original_tick
            await asyncio.sleep(.8)
            stopped_y = await page.evaluate('scrollY')
            await asyncio.sleep(.2)
            assert await page.evaluate('scrollY') == stopped_y, 'Stale audio kept scrolling'
            second = await browser.context.new_page()
            await second.goto((ROOT / 'ui/practice.html').as_uri())
            await second.bring_to_front()
            await second.locator('h1').click()
            await page.evaluate('window.__testFocus = false')
            await second.evaluate('window.__testFocus = true')
            await second.wait_for_function('document.hasFocus()')
            await feed(1.8)
            assert await second.evaluate('scrollY') > 20, {
                'focused': await second.evaluate('document.hasFocus()'),
                'states': [await p.evaluate('[document.hasFocus(), document.visibilityState]') for p in browser.context.pages],
                'direction': voice.detector.direction, 'candidate': voice.detector.candidate,
                'target': voice.target == second, 'task': voice.task.done()}
            assert await page.evaluate('scrollY') == stopped_y, 'Background tab scrolled'
            await page.bring_to_front()
            await page.locator('body').click(position={'x': 10, 'y': 10})
            await second.evaluate('window.__testFocus = false')
            await page.evaluate('window.__testFocus = true')
            await feed(.8)
            assert voice.target == page and browser.page == page, 'Existing tab not selected'
            voice.pause()
            # Allow one already-dispatched browser scroll step to finish.
            await asyncio.sleep(.1)
            paused_y = await page.evaluate('scrollY')
            await asyncio.sleep(.25)
            assert await page.evaluate('scrollY') == paused_y
            await page.reload()
            await page.evaluate('window.__testFocus = true')
            await asyncio.sleep(.2)
            assert await page.locator('#browser-voice-status').count() == 1
            assert 'Mic off' in await page.locator('#browser-voice-status span').inner_text()
            await page.screenshot(path=str(ROOT / 'artifacts/voice-status.png'))
        finally:
            await voice.close()
        # Page animation must stop autonomously if the controller stops updating.
        await page.bring_to_front()
        await page.locator('h1').click()
        await page.evaluate("window.__voiceTick({speed: 240, message: 'Watchdog test'})")
        await asyncio.sleep(.4)
        watchdog_y = await page.evaluate('scrollY')
        assert watchdog_y > 0, 'Animation never started'
        await asyncio.sleep(.2)
        assert await page.evaluate('scrollY') == watchdog_y, 'Animation outlived its heartbeat'
    print('PASS: generated audio scrolls, stale audio stops, tab targeting, pause, reload, and animation watchdog work')


if __name__ == '__main__':
    asyncio.run(main())
