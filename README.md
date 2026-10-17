# Browser studio

Visible Chromium controlled by Python: scrolling, keyboard focus, links, page reading, and image capture.

## Run

Install Python 3.10+, clone this repo, then run:

```sh
python run.py
```

First launch installs dependencies and Chromium (internet required). Type `help` for controls or `quit` to exit. Linux may also need Playwright's system dependencies installed.

- Home/themes/bookmarks: `ui/`
- Python controls: `browserkit/controller.py`
- Repair dependencies: `python run.py --setup`
- Windows smoke test: `.venv\Scripts\python.exe smoke_test.py`

Git shares source and requirements; each machine creates its own `.venv` and browser installation. Captures, caches, and secrets stay local. Themes style pages, not Chromium's native toolbar.
