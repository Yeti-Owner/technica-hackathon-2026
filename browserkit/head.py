"""Start the browser WITH head control, without changing the project's own code.

    python -m browserkit.head        (run.py can call this instead of `-m browserkit`)

It runs the project's normal command loop exactly as `python -m browserkit` does, and turns on head
tilt control as soon as the browser opens, with a small status bar at the top of every page:
    turn head right / left  -> Tab / Shift+Tab (always on)
    tilt head up / down     -> scroll (switch off with browser._head.set_scroll_enabled(False))
    hold ear to shoulder    -> toggles up/down head scrolling      (or press Ctrl+Shift+H)

The camera libraries are installed automatically the first time. If anything about head control
fails (no camera, install problem), the browser still starts normally and says why.
"""
import asyncio
import subprocess
import sys

from . import __main__ as app          # the project's own entry point (its main() is reused untouched)
from .controller import BrowserController
from .head_control import HeadControl

# The status bar. It is injected into every page, drawn inside a shadow root so page styles can't touch
# it, ignores the mouse, and can't be reached with Tab, so it never gets in the way of the page.
HUD_JS = r"""
(() => {
  if (window.top !== window || window.__headHudInstalled) return;
  window.__headHudInstalled = true;

  const CSS = `
    :host { all: initial; }
    .wrap { position: fixed; top: 10px; left: 50%; transform: translateX(-50%); z-index: 2147483647;
            display: flex; flex-direction: column; align-items: center; gap: 8px; pointer-events: none;
            font: 600 14px/1.1 "Segoe UI", system-ui, sans-serif; }
    .pill { display: flex; align-items: center; gap: 10px; padding: 9px 14px; border-radius: 999px;
            color: #fff; background: rgba(22, 24, 30, 0.93); border: 2px solid var(--c, #8aa0b5);
            box-shadow: 0 4px 18px rgba(0, 0, 0, 0.35); white-space: nowrap; transition: border-color 0.15s; }
    .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--c, #8aa0b5); }
    .chip { font-size: 12px; padding: 4px 9px; border-radius: 999px; }
    .chip.on { background: rgba(70, 190, 100, 0.25); color: #8ff0a8; }
    .chip.off { background: rgba(230, 80, 70, 0.28); color: #ffb0a9; }
    .toast { font-size: 18px; padding: 10px 20px; border-radius: 12px; color: #fff;
             background: rgba(22, 24, 30, 0.95); box-shadow: 0 4px 18px rgba(0, 0, 0, 0.35);
             opacity: 0; transform: translateY(-6px); transition: opacity 0.2s, transform 0.2s; }
    .toast.show { opacity: 1; transform: none; }
  `;
  const THEME = { CENTER: "#8aa0b5", LEFT: "#ffbf00", RIGHT: "#ffbf00", UP: "#4da3ff", DOWN: "#4da3ff",
                  WARN: "#e0504a", WAIT: "#b08cff" };

  let host, wrap, pill, msg, chip, toast, toastTimer, lastToggle = null;

  function mount() {
    if (host && host.isConnected) return true;
    const parent = document.documentElement;
    if (!parent) return false;
    host = document.createElement("div");
    host.id = "__head_hud";
    const root = host.attachShadow({ mode: "open" });
    try {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(CSS);
      root.adoptedStyleSheets = [sheet];
    } catch (e) {
      const style = document.createElement("style");
      style.textContent = CSS;
      root.appendChild(style);
    }
    wrap = document.createElement("div"); wrap.className = "wrap";
    pill = document.createElement("div"); pill.className = "pill";
    const dot = document.createElement("span"); dot.className = "dot";
    msg = document.createElement("span");
    chip = document.createElement("span"); chip.className = "chip";
    pill.append(dot, msg, chip);
    toast = document.createElement("div"); toast.className = "toast";
    wrap.append(pill, toast);
    root.appendChild(wrap);
    parent.appendChild(host);
    return true;
  }

  function showToast(text) {
    toast.textContent = text;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 1600);
  }

  function render(s) {
    if (!mount()) return;
    let key = s.dir, text;
    if (!s.alive) { key = "WARN"; text = "Head control stopped"; }
    else if (s.dir === "NO FACE") { key = "WARN"; text = "No face detected"; }
    else if (s.centering) { key = "WAIT"; text = "Centering… look straight ahead"; }
    else if (s.dir === "LEFT") text = "◀  Previous item";
    else if (s.dir === "RIGHT") text = "Next item  ▶";
    else if (s.dir === "UP") text = s.scroll ? "▲  Scrolling up" : "▲  Up (scroll is off)";
    else if (s.dir === "DOWN") text = s.scroll ? "▼  Scrolling down" : "▼  Down (scroll is off)";
    else { key = "CENTER"; text = "Head control on"; }
    pill.style.setProperty("--c", THEME[key] || THEME.CENTER);
    msg.textContent = text;
    chip.textContent = s.scroll ? "Scroll ON" : "Scroll OFF";
    chip.className = "chip " + (s.scroll ? "on" : "off");
    if (lastToggle !== null && s.toggles !== lastToggle) showToast(s.scroll ? "Head scroll ON" : "Head scroll OFF");
    lastToggle = s.toggles;
  }

  async function tick() {
    if (document.hidden || typeof window.__headState !== "function") return;
    try { render(await window.__headState()); } catch (e) { /* page is closing or navigating */ }
  }
  setInterval(tick, 120);
  tick();

  addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.shiftKey && !e.altKey && !e.metaKey && e.key.toLowerCase() === "h") {
      e.preventDefault();
      if (typeof window.__headCommand === "function") window.__headCommand("toggle-scroll");
    }
  }, true);
})();
"""


async def _install_hud(context, head):
    """Show the status bar on every page (and every new tab) for the whole session."""
    await context.expose_function("__headState", lambda: head.hud_state())
    await context.expose_function("__headCommand", lambda name: head.command(name))
    await context.add_init_script(script=HUD_JS)
    for page in list(context.pages):
        try:
            await page.reload()        # pages that are already open pick the bar up on reload
        except Exception:
            pass


_original_enter = BrowserController.__aenter__
_original_exit = BrowserController.__aexit__


async def _enter_with_head(self):
    result = await _original_enter(self)
    try:
        self._head = HeadControl(self, asyncio.get_running_loop())
        self._head.start()
    except Exception as error:         # never stop the browser from opening
        self._head = None
        print(f"[head] Head control is off: {error}")
        return result
    try:
        await _install_hud(self.context, self._head)
    except Exception as error:         # head control still works without the bar
        print(f"[head] Status bar unavailable: {error}")
    return result


async def _exit_with_head(self, *args):
    head = getattr(self, "_head", None)
    if head:
        head.stop()
    return await _original_exit(self, *args)


BrowserController.__aenter__ = _enter_with_head
BrowserController.__aexit__ = _exit_with_head


def _ensure_libraries():
    """Install the camera libraries into this Python environment the first time (never fatal)."""
    try:
        import cv2  # noqa: F401
        import mediapipe  # noqa: F401
        return
    except ImportError:
        pass
    print("[head] First run: installing head tracking libraries (can take a minute)...", flush=True)
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "mediapipe", "opencv-python"], check=True)
    except Exception as error:
        print(f"[head] Could not install head tracking libraries ({error}). The browser will run without head control.")


if __name__ == "__main__":
    _ensure_libraries()
    try:
        asyncio.run(app.main())
    except KeyboardInterrupt:
        pass
