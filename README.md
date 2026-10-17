# Browser studio

Python + Playwright Chromium with local voice scrolling. No audio API, AI model, or saved recordings.

## Run

Install Python 3.10+, then run `python run.py`. First launch installs dependencies and Chromium. Use `python run.py --setup` to repair setup. Linux may also need Playwright system dependencies and `libportaudio2`.

Hold **ooo** to scroll down or **aaa** to scroll up; these are vowels, not low/high pitch. Keep the page focused and pause between directions. Startup checks the default microphone and displays its name.

- `help` / `quit`: browser commands / exit.
- `mic on` / `mic off` / `mic status`: microphone controls.
- `mic list` / `mic use NUMBER` / `mic default`: input selection (session only).
- `mic reset`: clear learned vowel preferences.

## Code map

- `run.py`: local environment setup and launcher.
- `browserkit/__main__.py`: application lifecycle and terminal commands.
- `browserkit/controller.py`: browser control and scripts injected on navigation/new tabs.
- `browserkit/voice.py`: microphone capture, vowel detection, noise tracking, bounded adaptation.
- `browserkit/voice_control.py`: independent audio/browser loops, active-tab routing, microphone selection.
- `ui/voice.js`: status badge, frame-based scrolling, focus checks, controller watchdog.
- Other `ui/` files: home, practice page, themes, bookmarks, and shared page styling.

## Collaboration notes

- One microphone serves all tabs; audio stays in Python. Do not expose microphone access or Python command bindings to websites.
- Preserve hold continuity: spectral matching, 300 ms coast + 250 ms fade, and no reactivation after short gaps. Direction stays locked during a hold.
- Preserve focus checks and the page's 250 ms watchdog. Playwright focus emulation is disabled so background tabs do not scroll.
- Commit source and `requirements.txt`; keep environments, captures, secrets, and `artifacts/voice-profile.json` local.
- Recognition is experimental. Test real voices after detector changes; synthetic tests cannot establish accuracy. Browser-internal pages, PDFs, and cross-origin panels have scrolling limitations.

## Checks

Run using the project's environment (Windows shown; use `.venv/bin/python` on macOS/Linux):

```sh
.venv/Scripts/python.exe -m unittest test_voice -v
.venv/Scripts/python.exe smoke_test.py
.venv/Scripts/python.exe smoke_voice.py
```

Smoke tests open temporary browsers. Voice checks use generated audio and simulated tab focus, not your microphone or native OS focus.
