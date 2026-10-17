import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

html = """<!DOCTYPE html>
<html>
<head>
<style>
.container { position: relative; width: 400px; padding: 20px; }
.cloud-bg { position: absolute; inset: 0; pointer-events: none; }
.cloud-path { fill: #fff; stroke: rgb(200, 200, 200); stroke-width: 1.5px; }
.container:focus-within .cloud-path { stroke: rgb(2, 132, 199) !important; stroke-width: 3px !important; }
input { position: relative; z-index: 2; }
</style>
</head>
<body>
<div class="container">
  <svg class="cloud-bg" viewBox="0 0 100 100"><circle class="cloud-path" cx="50" cy="50" r="40"/></svg>
  <input type="text" id="my-in" />
</div>
</body>
</html>"""

Path("scratch/focus_test.html").write_text(html, encoding="utf-8")

async def check():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page()
        await page.goto(Path("scratch/focus_test.html").resolve().as_uri())
        await page.focus("#my-in")
        stroke = await page.evaluate("""() => {
            const p = document.querySelector('.cloud-path');
            return { stroke: window.getComputedStyle(p).stroke, width: window.getComputedStyle(p).strokeWidth };
        }""")
        print("Focus stroke result:", stroke)
        await b.close()

if __name__ == "__main__":
    asyncio.run(check())
