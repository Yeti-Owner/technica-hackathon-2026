"""Async Python control of a visible, disposable Chromium session."""
import json
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


class BrowserController:
    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        try:
            self.browser = await self.playwright.chromium.launch(headless=False)
            self.context = await self.browser.new_context(no_viewport=True)
            self.context.set_default_timeout(8000)
            # One script establishes order; runs again on navigation and in new tabs.
            css = (ROOT / "ui/inject.css").read_text(encoding="utf-8")
            script = (ROOT / "ui/inject.js").read_text(encoding="utf-8")
            script += "\n" + (ROOT / "ui/voice.js").read_text(encoding="utf-8")
            await self.context.add_init_script(script="window.__starterCSS = " + json.dumps(css) + ";\n" + script)
            self.context.on("page", self._select_page)
            self.page = await self.context.new_page()
            await self.home()
            return self
        except BaseException:
            await self.playwright.stop()
            raise

    def _select_page(self, page):
        self.page = page

    async def __aexit__(self, *args):
        try:
            await self.browser.close()
        finally:
            await self.playwright.stop()

    async def home(self):
        await self.page.goto((ROOT / "ui/home.html").as_uri())

    async def goto(self, url):
        await self.page.goto(url, wait_until="domcontentloaded")

    async def scroll(self, pixels=600):
        await self.page.mouse.wheel(0, pixels)

    async def next_focus(self):
        await self.page.keyboard.press("Tab")

    async def previous_focus(self):
        await self.page.keyboard.press("Shift+Tab")

    async def activate(self):
        await self.page.keyboard.press("Enter")

    async def click_link(self, name):
        await self.page.get_by_role("link", name=name, exact=True).click()

    async def text(self):
        return await self.page.locator("body").inner_text()

    async def links(self):
        return await self.page.locator("a[href]").evaluate_all("els => els.map(e => ({text: e.innerText, url: e.href}))")

    async def images(self):
        """Rendered IMG elements in all frames, including offscreen ones.

        Indices refer to each frame's img locator at the time of inspection.
        CSS backgrounds, SVG and canvas can be captured with capture_element().
        """
        result = []
        for frame_index, frame in enumerate(self.page.frames):
            items = await frame.locator("img").evaluate_all("""els => els.map((e, index) => {
                const r = e.getBoundingClientRect(), s = getComputedStyle(e);
                return {index, src: e.currentSrc || e.src, alt: e.alt,
                    width: e.naturalWidth, height: e.naturalHeight,
                    rendered: r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none',
                    inViewport: r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth};
            }).filter(e => e.rendered)""")
            result.extend(dict(item, frame=frame_index, frame_url=frame.url) for item in items)
        return result

    async def capture_image(self, index, path, frame=0):
        """Save rendered pixels, even for cross-origin images (no CORS fetch)."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        return await self.page.frames[frame].locator("img").nth(index).screenshot(path=str(target))

    async def capture_element(self, selector, path):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        return await self.page.locator(selector).screenshot(path=str(target))

    async def inject_css(self, css):
        """Current document only; ui/inject.css is installed on every navigation."""
        await self.page.add_style_tag(content=css)

    async def evaluate(self, javascript, argument=None):
        return await self.page.evaluate(javascript, argument)

    async def select_tab(self, index):
        self.page = self.context.pages[index]
        await self.page.bring_to_front()
