"""Head-tilt control for the Playwright BrowserController.

    Turn head RIGHT  -> controller.next_focus()      (Tab)
    Turn head LEFT   -> controller.previous_focus()  (Shift+Tab)
    Tilt head UP     -> controller.scroll(-px)       (only while head scroll is enabled)
    Tilt head DOWN   -> controller.scroll(+px)       (only while head scroll is enabled)
    Hold head rolled (ear to shoulder) ~1.2s -> toggles head scroll on/off

While head controls are enabled, left/right Tab navigation is on. Up/down scrolling can be handed
over to another input (like humming) with head.set_scroll_enabled(False).

Setup (once):  python -m pip install mediapipe opencv-python
The face model (~4 MB) downloads automatically on first run into your user folder (not the repo).

Quick camera test with no browser:   python head_control.py
"""
from __future__ import annotations

import asyncio
import base64
import math
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)
MODEL_PATH = Path.home() / ".cache" / "head-control" / "face_landmarker.task"  # outside the repo on purpose

DEFAULTS = dict(
    yaw_threshold=14.0,        # degrees to turn left/right before it counts
    pitch_threshold=10.0,      # degrees to tilt up/down before it counts
    release=0.65,              # easier to stay in a direction than to enter it
    dominance=1.4,             # stronger axis must beat the other by this factor
    smoothing=0.35,            # 0..1, lower = smoother but slower
    hold_frames=4,             # a new direction must hold this many frames
    yaw_sign=1,                # set -1 if left/right are backwards
    pitch_sign=1,              # set -1 if up/down are backwards
    min_speed=6,               # scroll pixels per tick at the threshold
    max_speed=70,              # fastest scroll pixels per tick
    gain=40,                   # extra speed per threshold of extra tilt
    tick_ms=33,                # scroll / Tab update rate
    tab_first_repeat_ms=700,   # hold a turn this long before Tab repeats
    tab_repeat_ms=450,         # then repeat this often
    enable_roll_toggle=True,   # hold ear to shoulder to toggle head scroll
    roll_threshold=22.0,       # degrees of roll that starts the toggle hold
    roll_guard=14.0,           # directions are ignored while rolled past this
    roll_hold_ms=1200,         # hold this long to toggle
    center_frames=20,          # frames averaged to find "looking straight ahead" (about 0.7 s)
    reacquire_seconds=3.0,     # if your face is gone this long, re-center when you return
    camera_index=0,
    verbose=False,             # also print every direction change to the terminal
)


class HeadControl:
    def __init__(self, controller, loop: asyncio.AbstractEventLoop, **overrides):
        unknown = set(overrides) - set(DEFAULTS)
        if unknown:
            raise TypeError(f"Unknown head control option(s): {sorted(unknown)}")
        self.cfg = types.SimpleNamespace(**{**DEFAULTS, **overrides})
        self.controller = controller
        self.loop = loop

        self._stop = threading.Event()
        self._thread = None
        self._scroll_on = True
        self._controls_on = True
        self._control_lock = threading.RLock()
        self._control_generation = 0
        self._actions = set()
        self._wait_for_center = False
        self._toggle_count = 0
        self._recalibrate = False
        self._last_ts = 0

        self._neutral = None
        self._center_buf = []
        self._face_lost_at = None
        self._latest = None
        self.current = "CENTER"
        self._candidate = "CENTER"
        self._candidate_frames = 0
        self._yaw_n = 0.0
        self._pitch_n = 0.0
        self._roll_start = 0.0
        self._roll_latched = False
        self._last_horiz = None
        self._tab_next = 0.0
        self._last_tick = 0.0
        self._scroll_future = None
        self._reported = ""
        self._preview_visible = True
        self._preview_frame = None
        self._preview_at = 0.0

    # ---------- public API ----------
    @property
    def controls_enabled(self):
        return self._controls_on

    def set_controls_enabled(self, enabled):
        with self._control_lock:
            self._controls_on = bool(enabled)
            self._control_generation += 1
            for future in list(self._actions):
                future.cancel()
            self.current = self._candidate = 'CENTER'
            self._candidate_frames = 0
            self._last_horiz = None
            self._tab_next = self._roll_start = 0.
            self._roll_latched = False
            self._latest = None
            self._wait_for_center = self._controls_on
        # Do not change the scroll preference, HUD toggle counter, or camera preview.
        print(f'[controls] Head controls {"ON — center your head to resume" if enabled else "OFF"}', flush=True)

    @property
    def scroll_enabled(self) -> bool:
        return self._scroll_on

    def set_scroll_enabled(self, on: bool):
        on = bool(on)
        if on != self._scroll_on:
            self._scroll_on = on
            self._toggle_count += 1
            self._say(f"Head scroll (up/down): {'ON' if on else 'OFF'}")

    def toggle_scroll(self):
        self.set_scroll_enabled(not self._scroll_on)

    def calibrate(self):
        """Re-center: look straight ahead for a moment."""
        self._recalibrate = True

    def command(self, name: str):
        """Used by the on-page status bar's keyboard shortcut."""
        if name == "toggle-scroll":
            self.toggle_scroll()
        elif name == "center":
            self.calibrate()
        elif name == "hide-preview":
            self._preview_visible = False
        elif name == "show-preview":
            self._preview_visible = True

    def hud_state(self) -> dict:
        """Everything the status bar needs, as plain data."""
        return {
            "alive": bool(self._thread and self._thread.is_alive()),
            "centering": self._neutral is None,
            "dir": self._reported or "CENTER",
            "scroll": self._scroll_on,
            "toggles": self._toggle_count,
            "previewVisible": self._preview_visible,
            "preview": self._preview_frame if self._preview_visible else None,
        }

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        try:
            import cv2  # noqa: F401
            import mediapipe  # noqa: F401
        except ImportError as error:
            raise ImportError("Head control needs: python -m pip install mediapipe opencv-python") from error
        self._ensure_model()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="head-control", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    # ---------- internals ----------
    def _say(self, text, chatty=False):
        if chatty and not self.cfg.verbose:
            return
        print(f"[head] {text}")

    @staticmethod
    def _ensure_model():
        if MODEL_PATH.exists():
            return
        print(f"[head] Downloading face model to {MODEL_PATH} ...")
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MODEL_PATH.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.replace(MODEL_PATH)

    def _pose(self, matrix):
        """(yaw, pitch, roll) in degrees from MediaPipe's 4x4 face pose matrix."""
        import numpy as np

        m = np.asarray(matrix, dtype=float).reshape(4, 4)
        if abs(m[3, :3]).sum() > 1e-3:   # some builds hand back the transpose; translation belongs in the last column
            m = m.T
        r = m[:3, :3]
        scale = np.linalg.norm(r[:, 2]) or 1.0
        r = r / scale
        yaw = math.degrees(math.atan2(r[0, 2], r[2, 2])) * self.cfg.yaw_sign
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, -r[1, 2])))) * self.cfg.pitch_sign
        roll = math.degrees(math.atan2(r[1, 0], r[1, 1]))
        return yaw, pitch, roll

    def _classify(self, dyaw, dpitch):
        """Exactly one of LEFT, RIGHT, UP, DOWN, CENTER."""
        c = self.cfg
        self._yaw_n = abs(dyaw) / c.yaw_threshold
        self._pitch_n = abs(dpitch) / c.pitch_threshold
        yaw_dir = "LEFT" if dyaw > 0 else "RIGHT"
        pitch_dir = "DOWN" if dpitch > 0 else "UP"
        in_yaw = self.current in ("LEFT", "RIGHT")
        in_pitch = self.current in ("UP", "DOWN")
        yaw_on = self._yaw_n >= (c.release if in_yaw else 1)
        pitch_on = self._pitch_n >= (c.release if in_pitch else 1)
        if not yaw_on and not pitch_on:
            return "CENTER"
        if yaw_on and not pitch_on:
            return yaw_dir
        if pitch_on and not yaw_on:
            return pitch_dir
        if self._yaw_n >= self._pitch_n * c.dominance:
            return yaw_dir
        if self._pitch_n >= self._yaw_n * c.dominance:
            return pitch_dir
        return yaw_dir if self._yaw_n >= self._pitch_n else pitch_dir

    def _on_pose(self, yaw, pitch, roll, now):
        with self._control_lock:
            if not self._controls_on:
                return
            self._update_pose(yaw, pitch, roll, now)

    def _update_pose(self, yaw, pitch, roll, now):
        c = self.cfg
        self._face_lost_at = None
        raw = (yaw, pitch, roll)
        if self._latest is None:
            self._latest = raw
        else:
            self._latest = tuple(l + (r - l) * c.smoothing for l, r in zip(self._latest, raw))

        if self._recalibrate:
            self._recalibrate = False
            self._neutral = None
            self._center_buf = []

        if self._neutral is None:
            # Averaging a short run of frames gives a steadier "straight ahead" than a single frame
            self._center_buf.append(self._latest)
            self.current = self._candidate = "CENTER"
            self._candidate_frames = 0
            self._reported = "CENTER"
            if len(self._center_buf) >= c.center_frames:
                n = len(self._center_buf)
                self._neutral = tuple(sum(p[i] for p in self._center_buf) / n for i in range(3))
                self._center_buf = []
                self._wait_for_center = False
                self._say("Centered. Head control is on.")
            return

        dyaw = self._latest[0] - self._neutral[0]
        dpitch = self._latest[1] - self._neutral[1]
        droll = self._latest[2] - self._neutral[2]

        if self._wait_for_center:
            centered = (abs(dyaw) < c.yaw_threshold * c.release and
                        abs(dpitch) < c.pitch_threshold * c.release and abs(droll) < c.roll_guard)
            self._candidate_frames = self._candidate_frames + 1 if centered else 0
            if self._candidate_frames >= c.hold_frames:
                self._wait_for_center = False
                self._candidate_frames = 0
            return

        if c.enable_roll_toggle:
            if abs(droll) >= c.roll_threshold:
                if not self._roll_start:
                    self._roll_start = now
                if not self._roll_latched and (now - self._roll_start) * 1000 >= c.roll_hold_ms:
                    self._roll_latched = True
                    self.toggle_scroll()
            else:
                self._roll_start = 0.0
                if abs(droll) < c.roll_guard:
                    self._roll_latched = False

        rolling = abs(droll) > c.roll_guard
        nxt = "CENTER" if rolling else self._classify(dyaw, dpitch)
        if nxt == self._candidate:
            self._candidate_frames += 1
        else:
            self._candidate, self._candidate_frames = nxt, 1
        if self._candidate != self.current and self._candidate_frames >= c.hold_frames:
            self.current = self._candidate
        self._report()

    def _on_no_face(self, now):
        if not self._controls_on:
            return
        self.current = self._candidate = "CENTER"
        if self._face_lost_at is None:
            self._face_lost_at = now
        elif self._neutral is not None and now - self._face_lost_at >= self.cfg.reacquire_seconds:
            self._neutral = None            # you were away: re-center when you come back
            self._center_buf = []
            self._latest = None
        if self._reported != "NO FACE":
            self._reported = "NO FACE"
            self._say("NO FACE", chatty=True)

    def _report(self):
        if self.current != self._reported:
            self._reported = self.current
            self._say(f"Head: {self.current}", chatty=True)

    def _speed(self, n):
        c = self.cfg
        return min(c.max_speed, c.min_speed + max(0.0, n - 1) * c.gain)

    def _submit(self, coro):
        try:
            return asyncio.run_coroutine_threadsafe(coro, self.loop)
        except RuntimeError:          # event loop already closed: shutting down
            coro.close()
            self._stop.set()
            return None

    @staticmethod
    def _log_failure(future):
        if future.cancelled():
            return
        try:
            future.result()
        except Exception as error:    # e.g. page closed or navigating
            print(f"[head] action skipped: {error}")

    def _fire(self, coro):
        with self._control_lock:
            if not self._controls_on or self._wait_for_center:
                coro.close()
                return None
            generation = self._control_generation
            started = [False]

            async def guarded():
                try:
                    if self._controls_on and generation == self._control_generation:
                        started[0] = True
                        await coro
                finally:
                    if not started[0]:
                        coro.close()

            future = self._submit(guarded())
            if future:
                self._actions.add(future)

                def finished(done):
                    if not started[0]:
                        coro.close()
                    with self._control_lock:
                        self._actions.discard(done)
                    self._log_failure(done)

                future.add_done_callback(finished)
            else:
                coro.close()
            return future

    def _tick(self, now):
        with self._control_lock:
            if not self._controls_on or self._wait_for_center:
                return
            self._dispatch_tick(now)

    def _dispatch_tick(self, now):
        c = self.cfg
        if now - self._last_tick < c.tick_ms / 1000:
            return
        self._last_tick = now

        # Left / right -> Tab navigation; repeats while controls are enabled and the turn is held
        horiz = self.current if self.current in ("LEFT", "RIGHT") else None
        if horiz:
            backwards = horiz == "LEFT"
            if horiz != self._last_horiz:
                self._fire(self.controller.previous_focus() if backwards else self.controller.next_focus())
                self._tab_next = now + c.tab_first_repeat_ms / 1000
            elif now >= self._tab_next:
                self._fire(self.controller.previous_focus() if backwards else self.controller.next_focus())
                self._tab_next = now + c.tab_repeat_ms / 1000
        self._last_horiz = horiz

        # Up / down -> scroll, faster the further you tilt (only while head scroll is on)
        if not self._scroll_on or self.current not in ("UP", "DOWN"):
            return
        if self._scroll_future is not None and not self._scroll_future.done():
            return                    # previous scroll still in flight; don't pile them up
        pixels = round(self._speed(self._pitch_n))
        self._scroll_future = self._fire(self.controller.scroll(-pixels if self.current == "UP" else pixels))

    def _run(self):
        try:
            import cv2
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision

            options = vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_buffer=MODEL_PATH.read_bytes()),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=1,
                output_facial_transformation_matrixes=True,
            )
            landmarker = vision.FaceLandmarker.create_from_options(options)
            backend = cv2.CAP_DSHOW if sys.platform == "win32" else 0
            cap = cv2.VideoCapture(self.cfg.camera_index, backend)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            if not cap.isOpened():
                print("[head] Could not open the camera (is another app using it?).")
                return
            self._say("Camera on. Look straight ahead to center.")
        except Exception as error:
            print(f"[head] Could not start head tracking: {error}")
            return

        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    self._preview_frame = None
                    time.sleep(0.01)
                    continue
                now = time.monotonic()
                # Reuse capture, with a small preview at no more than eight frames per second.
                if self._preview_visible and now - self._preview_at >= 0.125:
                    height, width = frame.shape[:2]
                    preview = cv2.resize(frame, (320, max(1, round(height * 320 / width))))
                    encoded, jpeg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    self._preview_frame = base64.b64encode(jpeg).decode("ascii") if encoded else None
                    self._preview_at = now
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                self._last_ts = max(self._last_ts + 1, int(time.monotonic() * 1000))
                result = landmarker.detect_for_video(image, self._last_ts)
                now = time.monotonic()
                if result.facial_transformation_matrixes:
                    self._on_pose(*self._pose(result.facial_transformation_matrixes[0]), now)
                else:
                    self._on_no_face(now)
                self._tick(now)
        except Exception as error:
            print(f"[head] Head tracking stopped: {error}")
        finally:
            self._preview_frame = None
            cap.release()
            landmarker.close()


if __name__ == "__main__":
    # Camera-only test: no browser, just prints what each head movement would do.
    class _Preview:
        _last = 0.0

        async def next_focus(self):
            print("  -> Tab (next item)")

        async def previous_focus(self):
            print("  -> Shift+Tab (previous item)")

        async def scroll(self, pixels):
            if time.monotonic() - self._last > 0.25:
                self._last = time.monotonic()
                print(f"  -> scroll {pixels:+d}px")

    async def _demo():
        head = HeadControl(_Preview(), asyncio.get_running_loop(), verbose=True)
        head.start()
        print("Running. Turn or tilt your head. Ctrl+C to quit.")
        try:
            await asyncio.Event().wait()
        finally:
            head.stop()

    try:
        asyncio.run(_demo())
    except KeyboardInterrupt:
        pass
