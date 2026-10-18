"""Start the browser WITH head control, without changing the project's own code.

    python -m browserkit.head        (run.py can call this instead of `-m browserkit`)

It runs the project's normal command loop exactly as `python -m browserkit` does, and turns on head
tilt control as soon as the browser opens, with a small status bar at the top of every page:
    turn head right / left  -> Tab / Shift+Tab (while head controls are enabled)
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
    .wrap { position: fixed; top: 86px; left: 50%; transform: translateX(-50%); z-index: 2147483647;
            display: flex; flex-direction: column; align-items: center; gap: 8px; pointer-events: none;
            font: 700 13px/1.2 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif; }
    .pill { display: flex; align-items: center; gap: 10px; padding: 7px 15px; border-radius: 999px;
            color: #081c28; background: rgba(255, 255, 255, 0.92); border: 1.5px solid var(--c, #8aa0b5);
            box-shadow: 0 6px 20px rgba(14, 52, 85, 0.14); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
            white-space: nowrap; transition: border-color 0.18s ease, box-shadow 0.18s ease; }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--c, #8aa0b5); }
    .chip { font-size: 11px; font-weight: 800; padding: 3px 8px; border-radius: 999px; letter-spacing: 0.04em; text-transform: uppercase; }
    .chip.on { background: rgba(22, 122, 54, 0.15); color: #12632b; }
    .chip.off { background: rgba(90, 105, 120, 0.15); color: #3a4b5c; }
    .toast { font-size: 14px; font-weight: 700; padding: 8px 18px; border-radius: 999px; color: #081c28;
             background: rgba(255, 255, 255, 0.95); box-shadow: 0 8px 24px rgba(14, 52, 85, 0.2);
             border: 1.5px solid rgba(255, 255, 255, 0.95); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
             opacity: 0; transform: translateY(-6px); transition: opacity 0.2s, transform 0.2s; }
    .toast.show { opacity: 1; transform: none; }
    .camera { position: fixed; top: 16px; right: 16px; z-index: 2147483647;
              width: min(240px, calc(100vw - 32px)); overflow: hidden; border-radius: 20px;
              color: #081c28; background: rgba(255,255,255,.92); border: 1.5px solid #8aa0b5;
              box-shadow: 0 6px 20px rgba(14,52,85,.14); backdrop-filter: blur(12px);
              font: 700 13px/1.2 'Plus Jakarta Sans', system-ui, sans-serif; }
    .camera.collapsed { width: auto; border-radius: 999px; }
    .camera button { display: flex; align-items: center; justify-content: space-between; gap: 16px;
                     width: 100%; padding: 12px 15px; border: 0; background: transparent;
                     color: inherit; font: inherit; cursor: pointer; }
    .camera button:hover { background: rgba(77,163,255,.12); }
    .camera button:focus-visible { outline: 3px solid #4da3ff; outline-offset: -4px; }
    .camera canvas { display: block; width: 100%; height: auto; transform: scaleX(-1); }
    .camera [hidden] { display: none; }
    .camera-status { padding: 24px 15px; text-align: center; color: #3a4b5c; }
  `;
  const THEME = { CENTER: "#8aa0b5", LEFT: "#ffbf00", RIGHT: "#ffbf00", UP: "#4da3ff", DOWN: "#4da3ff",
                  WARN: "#e0504a", WAIT: "#b08cff" };

  let host, wrap, pill, msg, chip, toast, toastTimer, lastToggle = null;
  let camera, cameraButton, canvas, cameraStatus, previewVisible = true, lastFrame = null;
  let polling = false, changingPreview = false;

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
    camera = document.createElement("section"); camera.className = "camera";
    camera.setAttribute("aria-label", "Camera preview");
    cameraButton = document.createElement("button"); cameraButton.type = "button";
    cameraButton.addEventListener("click", async () => {
      if (changingPreview) return;
      changingPreview = true;
      try {
        await window.__headCommand(previewVisible ? "hide-preview" : "show-preview");
        await tick();
      } finally { changingPreview = false; }
    });
    canvas = document.createElement("canvas"); canvas.hidden = true;
    canvas.setAttribute("role", "img"); canvas.setAttribute("aria-label", "Live camera preview");
    cameraStatus = document.createElement("div"); cameraStatus.className = "camera-status";
    camera.append(cameraButton, canvas, cameraStatus);
    root.appendChild(camera);
    lastFrame = null;
    parent.appendChild(host);
    return true;
  }

  function showToast(text) {
    toast.textContent = text;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 1600);
  }

  async function render(s) {
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
    previewVisible = s.previewVisible;
    camera.classList.toggle("collapsed", !previewVisible);
    cameraButton.textContent = previewVisible ? "Camera  −" : "Camera  +";
    cameraButton.setAttribute("aria-label", previewVisible ? "Hide camera preview" : "Show camera preview");
    cameraButton.setAttribute("aria-expanded", String(previewVisible));
    canvas.hidden = !previewVisible || !s.alive || !s.preview;
    cameraStatus.hidden = !previewVisible || (s.alive && !!s.preview);
    cameraStatus.textContent = s.alive ? "Waiting for camera…" : "Camera unavailable";
    if (previewVisible && s.alive && s.preview && s.preview !== lastFrame) {
      // Decode directly to a canvas so sites' image CSP does not block the preview.
      const bytes = Uint8Array.from(atob(s.preview), c => c.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/jpeg" }));
      canvas.width = bitmap.width; canvas.height = bitmap.height;
      canvas.getContext("2d").drawImage(bitmap, 0, 0);
      bitmap.close();
      lastFrame = s.preview;
    }
  }

  async function tick() {
    if (polling || document.hidden || typeof window.__headState !== "function") return;
    polling = true;
    try { await render(await window.__headState()); } catch (e) { /* page is closing or navigating */ }
    finally { polling = false; }
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
