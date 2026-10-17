# Browser studio

Visible Chromium controlled by Python: scroll, follow links, move focus with Tab/Shift+Tab, read pages, and capture images.

## Run

```powershell
.\.venv\Scripts\python.exe -m browserkit
```

First-time setup:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

Type `help` in the terminal for controls, or `quit` to exit.

## Customize

- `ui/home.html`, `ui/theme.css`: home page and themes.
- `ui/bookmarks.js`: bookmarks.
- `ui/inject.css`, `ui/inject.js`: page styling and behavior; restart after editing.
- `browserkit/controller.py`: Python browser controls.

Themes change page content; native Chromium tabs/address bar need a custom shell. Sessions aren't saved.

Test: `.\.venv\Scripts\python.exe smoke_test.py`
