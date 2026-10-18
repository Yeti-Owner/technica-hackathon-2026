"""Pause controls without hardware, including queued actions and neutral rearming."""
import asyncio
import queue
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numpy as np

from browserkit.controller import BrowserController
from browserkit.head_control import HeadControl
from browserkit.shortcuts import BrowserShortcuts
from browserkit.voice_control import VoiceControl


class ControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.browser = SimpleNamespace(click_focused=AsyncMock(return_value=True))

    async def test_voice_pause_clears_pending_audio_and_requires_new_confirmation(self):
        voice = VoiceControl(self.browser)
        voice.enabled = True
        voice.mic = SimpleNamespace(blocks=queue.Queue())
        voice.mic.blocks.put((time.monotonic(), np.ones(100), False))
        voice.window = np.ones(100)
        voice.pending = (time.monotonic(), 1, ('page', 'frame', 'key'))
        with patch('builtins.print'):
            voice.set_controls_enabled(False)
            await voice._dispatch_pending(('page', 'frame', 'key'))
            self.assertFalse(voice.controls_enabled)
            self.assertTrue(voice.mic.blocks.empty())
            self.assertEqual(len(voice.window), 0)
            voice.set_controls_enabled(True)
            await voice._dispatch_pending(('page', 'frame', 'key'))
        self.assertTrue(voice.enabled)  # No device restart.
        self.assertIsNone(voice.pending)
        self.assertIsNone(voice.candidate_target)
        self.browser.click_focused.assert_not_awaited()

    async def test_paused_audio_is_discarded_without_detection(self):
        voice = VoiceControl(self.browser)
        voice.enabled = True
        voice.mic = SimpleNamespace(blocks=queue.Queue())
        voice.gesture = Mock()
        with patch('builtins.print'):
            voice.set_controls_enabled(False)
        voice.mic.blocks.put((time.monotonic(), np.ones(100), False))
        task = asyncio.create_task(voice._audio_loop())
        await asyncio.sleep(.03)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(voice.mic.blocks.empty())
        voice.gesture.update.assert_not_called()

    async def test_voice_toggle_cancels_click_already_waiting_in_browser(self):
        ready = asyncio.Event()
        blocked = asyncio.Event()
        sent = Mock()

        async def click(*_):
            ready.set()
            await blocked.wait()
            sent()
            return True

        self.browser.click_focused = click
        voice = VoiceControl(self.browser)
        voice.enabled = True
        target = ('page', 'frame', 'key')
        voice.pending = (time.monotonic(), 1, target)
        task = asyncio.create_task(voice._dispatch_pending(target))
        await ready.wait()
        with patch('builtins.print'):
            voice.set_controls_enabled(False)
            voice.set_controls_enabled(True)
            blocked.set()
            await task
        sent.assert_not_called()

    async def test_voice_off_on_cannot_revive_a_click_waiting_for_focus_lock(self):
        controller = BrowserController()
        controller.focus_lock = asyncio.Lock()
        controller.voice_control_generation = 0
        voice = VoiceControl(controller)
        await controller.focus_lock.acquire()
        page = Mock()
        task = asyncio.create_task(controller.click_focused((page, None, 'key'), 'left'))
        await asyncio.sleep(0)
        with patch('builtins.print'):
            voice.set_controls_enabled(False)
            voice.set_controls_enabled(True)
        controller.focus_lock.release()
        self.assertFalse(await task)
        page.is_closed.assert_not_called()

    async def test_head_pause_blocks_tab_scroll_and_roll_without_changing_hud_toggle(self):
        head = HeadControl(self.browser, asyncio.get_running_loop(), smoothing=1)
        head._neutral = (0, 0, 0)
        head._fire = Mock()
        with patch('builtins.print'):
            head.set_controls_enabled(False)
            for direction in ['LEFT', 'RIGHT', 'UP', 'DOWN']:
                head.current = direction
                head._tick(time.monotonic())
            head._on_pose(35, 35, 35, time.monotonic())
            head._on_pose(35, 35, 35, time.monotonic() + 2)
        head._fire.assert_not_called()
        self.assertEqual(head._toggle_count, 0)
        self.assertTrue(head.scroll_enabled)

    async def test_head_resume_waits_for_center_and_preserves_scroll_preference(self):
        controller = SimpleNamespace(next_focus=AsyncMock(), previous_focus=AsyncMock(), scroll=AsyncMock())
        head = HeadControl(controller, asyncio.get_running_loop(), smoothing=1, hold_frames=2)
        head._neutral = (0, 0, 0)
        head._scroll_on = False
        with patch('builtins.print'):
            head.set_controls_enabled(False)
            head.set_controls_enabled(True)
        for i in range(5):
            head._on_pose(-30, 0, 0, i)
            head._tick(i)
        self.assertTrue(head._wait_for_center)
        controller.next_focus.assert_not_called()
        head._on_pose(0, 0, 0, 6)
        head._on_pose(0, 0, 0, 7)
        self.assertFalse(head._wait_for_center)
        head._on_pose(-30, 0, 0, 8)
        head._on_pose(-30, 0, 0, 9)
        head._tick(9)
        await asyncio.sleep(.02)
        controller.next_focus.assert_awaited_once()
        self.assertFalse(head.scroll_enabled)

    async def test_head_pause_cancels_queued_actions_even_after_resume(self):
        head = HeadControl(self.browser, asyncio.get_running_loop())
        blocked = asyncio.Event()
        sent = Mock()

        async def queued():
            await blocked.wait()
            sent()

        head._fire(queued())
        await asyncio.sleep(.01)
        with patch('builtins.print'):
            head.set_controls_enabled(False)
            head.set_controls_enabled(True)
        blocked.set()
        await asyncio.sleep(.02)
        sent.assert_not_called()

    async def test_head_cancellation_before_event_loop_dispatch(self):
        head = HeadControl(self.browser, asyncio.get_running_loop())
        action = AsyncMock()
        head._fire(action())
        with patch('builtins.print'):
            head.set_controls_enabled(False)
        await asyncio.sleep(.02)
        action.assert_not_awaited()

    async def test_router_toggles_independently_and_no_camera_is_safe(self):
        voice = VoiceControl(self.browser)
        shortcuts = BrowserShortcuts(self.browser, voice)
        with patch('builtins.print') as output:
            shortcuts.toggle('head')
            self.assertIn('--head', output.call_args.args[0])
            self.browser._head = HeadControl(self.browser, asyncio.get_running_loop())
            shortcuts.toggle('voice')
            self.assertFalse(voice.controls_enabled)
            self.assertTrue(self.browser._head.controls_enabled)
            shortcuts.toggle('head')
            self.assertFalse(self.browser._head.controls_enabled)
            self.assertFalse(voice.controls_enabled)


if __name__ == '__main__':
    unittest.main()
