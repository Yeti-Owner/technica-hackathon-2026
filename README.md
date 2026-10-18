# Browser studio

Python + Playwright Chromium browser studio: terminal commands, camera head control, local vowel clicks, and in-browser AI image detection. Camera and microphone processing stay local; there are no cloud audio APIs.

## Run

Install Python 3.10+, then run `python run.py`. First launch bootstraps the local `.venv`, installs `requirements.txt` and Playwright Chromium. Camera mode downloads a ~4 MB face model to `~/.cache/head-control` on first use. Use `python run.py --setup` to re-run setup. Linux may also need Playwright system dependencies and `libportaudio2`.

`python run.py` launches without camera/head control. Use `python run.py --head` to enable it. Both modes include voice clicks and the AI image check when configured; use Tab/Shift+Tab to select elements without head control.

## Setup (.env)

The AI image check needs a server and key. Copy `.env.example` to `.env` and fill in:

- `AI_CHECK_SERVER_URL` — the check server address; it must start with `http://`.
- `AI_CHECK_API_KEY` — the key the server checks in the `X-API-Key` header.

Real environment variables override `.env`. Restart the app to load values. Without this, the AI check idles with a message and everything else runs normally.

## Browser control shortcuts (Windows)

While the Chromium window launched by this app is focused:

- **Ctrl+Alt+V** toggles detection of `ooo` / `aaa` voice clicks.
- **Ctrl+Alt+H** toggles all camera actions: forward/backward focus, scrolling, and the head-roll gesture.

The shortcuts work in every tab, frames, and the address bar. Each physical press toggles once; holding the keys does not repeat. Only the terminal prints the new state—no new page badges, toasts, or overlays. Other applications and separate browser instances are unaffected.

Pausing cancels unfinished and queued actions. Returning to voice control requires a fresh quiet pause and vowel hold; returning to head control requires centering your head first. These switches pause control processing without closing the microphone or camera, and preserve the separate head-scroll setting. They do not start a missing camera: launch with `--head` to enable head tracking. On other operating systems, the existing terminal microphone commands and head-scroll controls remain available.

## Head control

- Turn head right / left → next / previous focus (Tab / Shift+Tab, while head controls are on).
- Tilt head up / down → scroll, while head scroll is enabled.
- Hold head rolled (ear to shoulder) ~1.2 s, or press Ctrl+Shift+H, to toggle head scrolling.
- A status pill at the top of every page shows the current direction and whether scrolling is on.
- With `--head`, a mirrored camera preview appears in the top-right corner. Click its header to hide or show it; your choice follows navigation and new tabs for the session. Hiding it leaves head control running.

Camera frames are processed locally by MediaPipe; nothing is uploaded.

## AI image check

- `ai on` / `ai off` / `ai status` / `ai concurrency 1-3`: control the checker.
- Flagged images get a red banner/outline in the page.
- `ai-check.json` tunes thresholds and limits.
- The redesigned home (Tomas's website 2.0: sun/moon/clouds theme, cloud nav, founders section, GSAP/Lenis animations loaded from CDNs — internet needed for full effect, degrades gracefully offline) has a "Try Practice Tools" button that opens the AI image lab, a debug page listing the images from `test_images.txt`.

Data flow: image bytes (or URLs) are POSTed to the configured server with the `X-API-Key` header.

## Voice clicks

Head left/right (or Tab/Shift+Tab) selects an element. After a brief quiet pause, hold **ooo** steadily for about **half a second** to left-click it, or **aaa** to right-click it. Each utterance produces at most one click; pause for at least 0.3 seconds before another. Detection gaps up to 0.24 seconds preserve progress but do not count toward confirmation. Focus changes, missing audio, and sustained changing sounds cancel unfinished confirmation. Completed detections print either a click-sent message or a reason for skipping. Right click opens the element's context menu; it does not choose a menu action.

`mic on/off/status`, `mic list`, `mic use NUMBER`, `mic default`, and `mic reset` control input and preferences. Startup checks the default microphone. No voice overlay or voice scrolling is enabled. Detection rejects brief syllables and requires stable, clear vowels above background noise, but prolonged vowels in speech or speaker playback can still trigger it; this is not a speech-intent classifier.

## Code map

- `browserkit/shortcuts.py`: native Windows shortcuts scoped to this app's Chromium process; no page-accessible command bindings.

- `run.py`: local environment setup and launcher (camera off by default; `--head` enables it).
- `browserkit/__main__.py`: application lifecycle and terminal commands.
- `browserkit/controller.py`: browser control and scripts injected on navigation/new tabs.
- `browserkit/head.py`: head-mode launcher; attaches head control and the status HUD.
- `browserkit/head_control.py`: camera capture and head-tilt detection (MediaPipe).
- `browserkit/ai_check.py`: passive AI image checking (sniff responses, ask the server, mark verdicts).
- `ai-check.json`: AI check configuration.
- `browserkit/voice.py`: reusable vowel processing and stricter one-shot `ClickDetector`.
- `browserkit/voice_control.py`, `ui/voice_click.js`: audio lifecycle, focus identity, and left/right click dispatch. The legacy `ui/voice.js` scroll script has been removed; humming is mapped to clicks only.
- `ui/ai_scan.js`, `ui/ai_mark.css`: in-page AI scan and marking.
- `ui/navigation-demo.html`: standalone camera/keyboard navigation demo with local click feedback; open the file directly. It is intentionally absent from site navigation.
- `ui/image-lab.html`: debug lab; its card list is generated from `test_images.txt` at browser start.
- `ui/home.html`, `ui/home.js`, `ui/theme.css`: redesigned home (website 2.0) — sun/moon/clouds theme, cloud nav, founders section, GSAP/Lenis animations from CDNs.
- `ui/assets/`: runtime images (sun, moon, clouds, logos). `Images for ui/` preserves original artwork and team photos; several are referenced directly by the home page. The redundant root `assets/` copy was removed.
- `scratch/`: frontend preview and verification scripts. Generated screenshots and HTML previews are ignored; rerun the corresponding script to recreate them.
- `CLAUDEwebdesign copy.md`, `Founders Descriptions.md`, `website description.md`: design and notes docs.
- Other `ui/` files: practice page (reachable by direct URL only) and shared page styling.

## Collaboration notes

- One microphone serves all tabs; audio stays in Python. Do not expose microphone access or Python command bindings to websites.
- Clicks must use fresh confirmation, never the legacy scrolling hold/fade. Preserve one click per utterance, quiet rearming, and cancellation when focus changes; never retry an uncertain click.
- Learning must preserve confident default matches. Hold references refresh only from confirmed vowels; room-noise samples expire and reset when the microphone starts.
- Preserve real focus checks and target identity. Playwright focus emulation is disabled so voice clicks target the active page; serialize clicks with head/keyboard focus movement.
- Commit source and `requirements.txt`; keep environments, captures, secrets, and `artifacts/voice-profile.json` local.
- Recognition is experimental. Test real voices after detector changes; synthetic tests cannot establish accuracy. Browser-internal pages, PDFs, and cross-origin panels have scrolling limitations.
- AI image check: image bytes/URLs are sent to the configured server with the API key; keep `.env` local and never commit it.
- Head control: camera frames are processed locally by MediaPipe; the face model is cached under `~/.cache/head-control` and nothing is uploaded.

## Checks

Run using the project's environment (Windows shown; use `.venv/bin/python` on macOS/Linux):

```sh
.venv/Scripts/python.exe -m unittest test_voice -v
.venv/Scripts/python.exe -m unittest test_voice_click -v
.venv/Scripts/python.exe -m unittest test_voice_dispatch -v
.venv/Scripts/python.exe -m unittest test_ai_check -v
.venv/Scripts/python.exe -m unittest test_control_shortcuts -v
.venv/Scripts/python.exe smoke_test.py
.venv/Scripts/python.exe smoke_ai.py
.venv/Scripts/python.exe smoke_voice.py
.venv/Scripts/python.exe smoke_shortcuts.py
```

Smoke tests open temporary browsers. `smoke_voice.py` uses generated audio and simulated window focus to verify trusted left/right clicks and cancellation; it does not measure real-speech accuracy or native OS focus.

`smoke_shortcuts.py` is Windows-only and opens temporary Chromium windows to send native shortcut keypresses; it does not open the microphone or camera.
