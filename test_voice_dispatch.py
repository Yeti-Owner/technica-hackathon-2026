"""Confirmed sounds must be dispatched once or explain why they were skipped."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from playwright.async_api import Error
from browserkit.voice_control import VoiceControl


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.browser = SimpleNamespace(click_focused=AsyncMock(return_value=True))
        self.voice = VoiceControl(self.browser)
        self.voice.enabled = True
        self.target = ('page', 'frame', 'element')
        self.voice.pending = (time.monotonic()-.3, 1, self.target)

    async def test_confirmed_click_survives_audio_gap_and_browser_poll_delay(self):
        self.voice._cancel(preserve_pending=True)
        with patch('builtins.print') as output:
            await self.voice._dispatch_pending(self.target)
            await self.voice._dispatch_pending(self.target)
        self.browser.click_focused.assert_awaited_once_with(self.target, 'left')
        output.assert_called_once_with('Voice left click sent', flush=True)

    async def test_focus_change_and_expired_click_are_not_sent(self):
        with patch('builtins.print') as output:
            await self.voice._dispatch_pending(('other', 'frame', 'element'))
            self.assertIn('focus changed', output.call_args.args[0])
            self.voice.pending = (time.monotonic()-2, 1, self.target)
            await self.voice._dispatch_pending(self.target)
            self.assertIn('too long', output.call_args.args[0])
        self.browser.click_focused.assert_not_awaited()

    async def test_unavailable_target_and_click_errors_explain_skip_without_retry(self):
        with patch('builtins.print') as output:
            self.browser.click_focused.return_value = False
            await self.voice._dispatch_pending(self.target)
            self.assertIn('lost focus', output.call_args.args[0])
            self.voice.pending = (time.monotonic(), -1, self.target)
            self.browser.click_focused.side_effect = Error('covered')
            await self.voice._dispatch_pending(self.target)
            self.assertIn('covered', output.call_args.args[0])
            await self.voice._dispatch_pending(self.target)
        self.assertEqual(self.browser.click_focused.await_count, 2)

    async def test_pause_cancels_confirmed_click_with_explanation(self):
        with patch('builtins.print') as output:
            self.voice.pause()
            await self.voice._dispatch_pending(self.target)
            self.assertIn('paused', output.call_args.args[0])
        self.browser.click_focused.assert_not_awaited()
