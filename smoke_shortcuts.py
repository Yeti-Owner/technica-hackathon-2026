"""Windows-only native keyboard smoke check; no camera or microphone required."""
import asyncio
import ctypes
import sys
from ctypes import wintypes
from types import SimpleNamespace

from playwright.async_api import async_playwright

from browserkit.shortcuts import BrowserShortcuts


class FakeControl:
    def __init__(self):
        self.controls_enabled = True
        self.changes = []

    def set_controls_enabled(self, enabled):
        self.controls_enabled = enabled
        self.changes.append(enabled)


async def process_id(browser):
    session = await browser.new_browser_cdp_session()
    try:
        info = await session.send('SystemInfo.getProcessInfo')
        return next(int(p['id']) for p in info['processInfo'] if p['type'] == 'browser')
    finally:
        await session.detach()


def foreground(pid):
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def collect(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            windows.append(hwnd)
        return True

    user32.EnumWindows(collect, 0)
    assert windows, 'No visible browser window'
    user32.SetForegroundWindow(windows[0])
    active = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(active))
    assert active.value == pid, 'Could not focus the test browser; no keys sent'


async def chord(key, *, repeats=1, alt=True):
    send = ctypes.windll.user32.keybd_event
    modifiers = [0x11, 0x12] if alt else [0x11]
    try:
        for modifier in modifiers:
            send(modifier, 0, 0, 0)
        for _ in range(repeats):
            send(ord(key), 0, 0, 0)
            await asyncio.sleep(.03)
    finally:
        send(ord(key), 0, 2, 0)
        for modifier in reversed(modifiers):
            send(modifier, 0, 2, 0)
    await asyncio.sleep(.15)


async def main():
    if sys.platform != 'win32':
        print('SKIP: native shortcut test requires Windows')
        return
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        other = None
        voice, head = FakeControl(), FakeControl()
        shortcuts = BrowserShortcuts(SimpleNamespace(browser=browser, _head=head), voice)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.set_content('<input aria-label="Typing"><button>Test</button><iframe srcdoc="<input>"></iframe>')
            await shortcuts.start()
            assert shortcuts._listener and shortcuts._listener._error is None
            pid = await process_id(browser)
            foreground(pid)
            await page.locator('input').focus()
            await chord('V', repeats=5)
            assert voice.changes == [False], voice.changes
            assert await page.locator('input').input_value() == ''

            # New tabs and navigations keep the same native listener and control state.
            tab = await context.new_page()
            await tab.goto('data:text/html,<input aria-label="Second tab">')
            foreground(pid)
            await chord('H')
            assert head.changes == [False]
            await tab.goto('about:blank')
            await chord('L', alt=False)  # Browser address bar, outside the document.
            await chord('V')
            assert voice.changes == [False, True]
            await page.bring_to_front()
            foreground(pid)
            await page.frame_locator('iframe').locator('input').focus()
            await chord('H')
            assert head.changes == [False, True]

            # JavaScript-generated key events cannot toggle hardware controls.
            await page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key:'v',ctrlKey:true,altKey:true,bubbles:true}))")
            await asyncio.sleep(.1)
            assert voice.changes == [False, True]

            # Another Chromium process must keep its own shortcuts.
            other = await playwright.chromium.launch(headless=False)
            other_page = await other.new_page()
            await other_page.goto('about:blank')
            foreground(await process_id(other))
            await chord('H')
            assert head.changes == [False, True]
            await other.close()
            other = None

            foreground(pid)
            await shortcuts.close()
            await chord('V')
            assert voice.changes == [False, True]
            print('PASS: native shortcuts, repeat suppression, tabs, navigation, address bar, iframe, process isolation, and shutdown')
        finally:
            await shortcuts.close()
            if other:
                await other.close()
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
