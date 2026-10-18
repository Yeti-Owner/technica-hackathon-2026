"""Native shortcuts scoped to this app's Chromium process, not website JavaScript."""
import asyncio
import sys
import threading


class BrowserShortcuts:
    def __init__(self, browser, voice):
        self.browser = browser
        self.voice = voice
        self._listener = None

    async def start(self):
        if sys.platform != 'win32':
            print('[controls] Browser-wide shortcuts are available on Windows only.', flush=True)
            return
        session = None
        try:
            session = await self.browser.browser.new_browser_cdp_session()
            info = await session.send('SystemInfo.getProcessInfo')
            pid = next(int(p['id']) for p in info['processInfo'] if p['type'] == 'browser')
            loop = asyncio.get_running_loop()
            self._listener = WindowsBrowserKeys(pid, lambda action: loop.call_soon_threadsafe(self.toggle, action))
            await asyncio.to_thread(self._listener.start)
            print('[controls] Ctrl+Alt+V: voice on/off | Ctrl+Alt+H: head control on/off', flush=True)
        except Exception as error:
            await self.close()
            print(f'[controls] Shortcuts unavailable: {error}', flush=True)
        finally:
            if session:
                await session.detach()

    def toggle(self, action):
        if action == 'voice':
            self.voice.set_controls_enabled(not self.voice.controls_enabled)
        elif action == 'head':
            head = getattr(self.browser, '_head', None)
            if head is None:
                print('[controls] Head control is unavailable. Start with --head to enable the camera.', flush=True)
            else:
                head.set_controls_enabled(not head.controls_enabled)

    async def close(self):
        if self._listener:
            await asyncio.to_thread(self._listener.stop)
            self._listener = None


class WindowsBrowserKeys:
    """Listen only for two chords while the launched Chromium owns foreground focus.

    A native hook also works in the address bar, browser menus and cross-origin
    frames. No keys are recorded and other applications retain their shortcuts.
    """
    def __init__(self, browser_pid, callback):
        self.browser_pid = browser_pid
        self.callback = callback
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self._error = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name='browser-shortcuts', daemon=True)
        self._thread.start()
        if not self._ready.wait(3):
            raise RuntimeError('Keyboard listener did not start')
        if self._error:
            raise self._error

    def stop(self):
        if self._thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL('user32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        result_type = ctypes.c_ssize_t
        hook_proc_type = ctypes.WINFUNCTYPE(result_type, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        class KeyboardEvent(ctypes.Structure):
            _fields_ = [('vkCode', wintypes.DWORD), ('scanCode', wintypes.DWORD),
                        ('flags', wintypes.DWORD), ('time', wintypes.DWORD),
                        ('dwExtraInfo', ctypes.c_size_t)]

        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hook_proc_type, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HANDLE
        user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = result_type
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        pressed = set()
        hook = None

        def held(key):
            return bool(user32.GetAsyncKeyState(key) & 0x8000)

        @hook_proc_type
        def handle(code, message, pointer):
            if code == 0:
                key = ctypes.cast(pointer, ctypes.POINTER(KeyboardEvent)).contents.vkCode
                if key in (0x56, 0x48):  # V / H; ignore all other key contents.
                    if message in (0x0101, 0x0105) and key in pressed:
                        pressed.discard(key)
                        return 1
                    if message in (0x0100, 0x0104):
                        if key in pressed:
                            return 1  # One toggle per physical press, never key repeat.
                        pid = wintypes.DWORD()
                        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
                        chord = held(0x11) and held(0x12) and not any(held(k) for k in (0x10, 0x5B, 0x5C, 0xA5))
                        if pid.value == self.browser_pid and chord:
                            pressed.add(key)
                            self.callback('voice' if key == 0x56 else 'head')
                            return 1
            return user32.CallNextHookEx(hook, code, message, pointer)

        try:
            self._thread_id = kernel32.GetCurrentThreadId()
            message = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)  # Create the thread message queue.
            hook = user32.SetWindowsHookExW(13, handle, kernel32.GetModuleHandleW(None), 0)
            if not hook:
                raise ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as error:
            self._error = error
            self._ready.set()
        finally:
            if hook:
                user32.UnhookWindowsHookEx(hook)
