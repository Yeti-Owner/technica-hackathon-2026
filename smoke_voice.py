"""Offline integration: generated audio + real trusted clicks on a local fixture."""
import asyncio
import queue
import time
from types import SimpleNamespace

import numpy as np
from scipy import signal
from browserkit import BrowserController
from browserkit.controller import ROOT
from browserkit.voice import ClickDetector, VowelDetector
from browserkit.voice_control import VoiceControl


def sound(formants, seconds, pitch=140):
    rate = 48000
    t = np.arange(int(rate*(seconds+.15)))/rate
    x = signal.sawtooth(2*np.pi*pitch*t)
    for frequency, bandwidth in zip(formants, [70, 100, 150]):
        radius = np.exp(-np.pi*bandwidth/rate)
        x = signal.lfilter([1], [1, -2*radius*np.cos(2*np.pi*frequency/rate), radius**2], x)
    x = x[-int(rate*seconds):]
    return .08*x/max(np.max(np.abs(x)), 1e-12)


async def main():
    async with BrowserController() as browser:
        page = browser.page
        await page.set_content('''<button id="one">One</button><button id="two">Two</button>
            <button id="disabled" disabled>Disabled</button>
            <script>
                window.events = [];
                for (const name of ['click', 'contextmenu']) document.addEventListener(name, e => {
                    events.push([name, e.target.id, e.isTrusted]); e.preventDefault();
                });
                window.testFocused = true;
                Object.defineProperty(document, 'hasFocus', {value: () => window.testFocused});
                window.addEventListener('blur', e => {
                    if (e.target === window) e.stopImmediatePropagation();
                }, true); // Simulated focus must not depend on the user's OS window.
            </script>''')
        # set_content replaces document listeners without a navigation/init-script run.
        await page.add_script_tag(path=str(ROOT / 'ui/voice_click.js'))
        await page.locator('#one').focus()
        voice = VoiceControl(browser)
        voice.detector = VowelDetector()
        voice.gesture = ClickDetector(voice.detector)
        voice.mic = SimpleNamespace(rate=48000, blocks=queue.Queue(), close=lambda: None)
        voice.enabled = True
        voice.task = asyncio.create_task(voice.run())
        for _ in range(100):
            if voice.target:
                break
            await asyncio.sleep(.02)
        assert voice.target, 'Focus monitoring did not initialize'

        async def feed(samples):
            for start in range(0, len(samples), 1440):
                voice.mic.blocks.put((time.monotonic(), samples[start:start+1440], False))
                await asyncio.sleep(.03)
            await asyncio.sleep(.04)

        async def quiet():
            await feed(np.zeros(38400))

        try:
            await quiet()
            await feed(sound([350, 850, 2500], 1.5))
            assert await page.evaluate('events') == [['click', 'one', True]], (await page.evaluate('events'), voice.status())
            await quiet()
            await browser.next_focus()  # Same API called by rightward head movement.
            await quiet()
            await feed(sound([800, 1300, 2600], 1.8))
            assert await page.evaluate('events') == [['click', 'one', True], ['contextmenu', 'two', True]], voice.status()
            await quiet()
            pending_sound = asyncio.create_task(feed(sound([350, 850, 2500], 1.2)))
            await asyncio.sleep(.3)
            await browser.previous_focus()
            await pending_sound
            assert len(await page.evaluate('events')) == 2, 'Focus change did not cancel'
            await quiet()
            pending_sound = asyncio.create_task(feed(sound([350, 850, 2500], 1.2)))
            await asyncio.sleep(.3)
            voice.pause()
            await pending_sound
            assert len(await page.evaluate('events')) == 2, 'Pause did not cancel'
            assert await page.locator('#browser-voice-status').count() == 0
            assert await page.evaluate('window.__voiceTick === undefined')
        finally:
            await voice.close()
    print('PASS: left/right trusted clicks, one click per hold, head focus integration, focus-change/pause cancellation, no scrolling overlay')


if __name__ == '__main__':
    asyncio.run(main())
