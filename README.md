# Browser studio

Python + Playwright Chromium browser studio: terminal commands, camera head control (on by default), and in-browser AI image detection. The local voice/humming stack is present but currently disabled (reserved for future repurpose). Camera frames are processed locally; there are no cloud audio APIs.

## Run

Install Python 3.10+, then run `python run.py`. First launch bootstraps the local `.venv`, installs `requirements.txt` (including mediapipe and opencv-python), installs Playwright Chromium, and downloads a ~4 MB face model to `~/.cache/head-control`. Use `python run.py --setup` to re-run setup. Linux may also need Playwright system dependencies and `libportaudio2`.

`python run.py` launches head mode by default: the browser plus camera head control, and the AI image check when it is configured. To run without head control, use `python -m browserkit`.

## Setup (.env)

The AI image check needs a server and key. Copy `.env.example` to `.env` and fill in:

- `AI_CHECK_SERVER_URL` — the check server address; it must start with `http://`.
- `AI_CHECK_API_KEY` — the key the server checks in the `X-API-Key` header.

Real environment variables override `.env`. Restart the app to load values. Without this, the AI check idles with a message and everything else runs normally.

## Head control

- Turn head right / left → next / previous focus (Tab / Shift+Tab, always on).
- Tilt head up / down → scroll, while head scroll is enabled.
- Hold head rolled (ear to shoulder) ~1.2 s, or press Ctrl+Shift+H, to toggle head scrolling.
- A status pill at the top of every page shows the current direction and whether scrolling is on.

Camera frames are processed locally by MediaPipe; nothing is uploaded.

## AI image check

- `ai on` / `ai off` / `ai status` / `ai concurrency 1-3`: control the checker.
- Flagged images get a red banner/outline in the page.
- `ai-check.json` tunes thresholds and limits.
- The home page's "AI image lab" bookmark opens a debug page listing the images from `test_images.txt`.

Data flow: image bytes (or URLs) are POSTed to the configured server with the `X-API-Key` header.

## Voice (currently disabled)

The local voice/humming stack is present but disabled by design: `VoiceControl.start()` is a no-op and `ui/voice.js` is not injected, so the `mic *` commands currently do nothing. It is reserved for future repurpose.

## Code map

- `run.py`: local environment setup and launcher (head mode by default).
- `browserkit/__main__.py`: application lifecycle and terminal commands.
- `browserkit/controller.py`: browser control and scripts injected on navigation/new tabs.
- `browserkit/head.py`: head-mode launcher; attaches head control and the status HUD.
- `browserkit/head_control.py`: camera capture and head-tilt detection (MediaPipe).
- `browserkit/ai_check.py`: passive AI image checking (sniff responses, ask the server, mark verdicts).
- `ai-check.json`: AI check configuration.
- `browserkit/voice.py`, `browserkit/voice_control.py`, `ui/voice.js`: voice/humming stack (currently disabled).
- `ui/ai_scan.js`, `ui/ai_mark.css`: in-page AI scan and marking.
- `ui/image-lab.html`: debug lab; its card list is generated from `test_images.txt` at browser start.
- Other `ui/` files: home, practice page, themes, bookmarks, and shared page styling.

## Collaboration notes

- One microphone serves all tabs; audio stays in Python. Do not expose microphone access or Python command bindings to websites.
- Preserve hold continuity: spectral matching, 300 ms coast + 250 ms fade, and no reactivation after short gaps. Direction stays locked during a hold.
- Learning must preserve confident default matches. Hold references refresh only from confirmed vowels; room-noise samples expire and reset when the microphone starts.
- Preserve focus checks and the page's 250 ms watchdog. Playwright focus emulation is disabled so background tabs do not scroll.
- Commit source and `requirements.txt`; keep environments, captures, secrets, and `artifacts/voice-profile.json` local.
- Recognition is experimental. Test real voices after detector changes; synthetic tests cannot establish accuracy. Browser-internal pages, PDFs, and cross-origin panels have scrolling limitations.
- AI image check: image bytes/URLs are sent to the configured server with the API key; keep `.env` local and never commit it.
- Head control: camera frames are processed locally by MediaPipe; the face model is cached under `~/.cache/head-control` and nothing is uploaded.

## Checks

Run using the project's environment (Windows shown; use `.venv/bin/python` on macOS/Linux):

```sh
.venv/Scripts/python.exe -m unittest test_voice -v
.venv/Scripts/python.exe -m unittest test_ai_check -v
.venv/Scripts/python.exe smoke_test.py
.venv/Scripts/python.exe smoke_ai.py
.venv/Scripts/python.exe smoke_voice.py
```

Smoke tests open temporary browsers. Voice checks use generated audio and simulated tab focus, not your microphone or native OS focus. `smoke_voice.py` is a dormancy guard: it passes while the voice stack is disabled.
