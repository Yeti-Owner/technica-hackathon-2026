"""Offline preview integration check using a synthetic frame, without opening a camera."""
import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

from playwright.async_api import async_playwright, expect

from browserkit.head import _install_hud
from browserkit.head_control import HeadControl


async def main():
    head = HeadControl(None, asyncio.get_running_loop())
    head._thread = SimpleNamespace(is_alive=lambda: True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        # Generate a valid frame in Chromium, avoiding camera/model dependencies.
        head._preview_frame = await page.evaluate("""() => {
            const c = document.createElement('canvas'); c.width = 320; c.height = 240;
            const ctx = c.getContext('2d'); ctx.fillStyle = '#4da3ff'; ctx.fillRect(0, 0, 320, 240);
            return c.toDataURL('image/jpeg').split(',')[1];
        }""")
        assert base64.b64decode(head._preview_frame)
        # Two different origins, with restrictive CSP, exercise real document replacement.
        await context.route("https://preview-*.test/**", lambda route: route.fulfill(
            body="<html><body><h1>Preview test</h1></body></html>",
            content_type="text/html", headers={"Content-Security-Policy": "default-src 'none'"}))
        await _install_hud(context, head)
        await page.goto("https://preview-one.test/")
        canvas = page.locator("#__head_hud canvas")
        await expect(canvas).to_be_visible()
        await page.wait_for_function("document.querySelector('#__head_hud').shadowRoot.querySelector('canvas').width === 320")
        assert await canvas.evaluate("c => c.getContext('2d').getImageData(10,10,1,1).data[2] > 200")
        box = await page.locator("#__head_hud .camera").bounding_box()
        assert box["x"] > 1000 and box["y"] == 16
        await page.get_by_role("button", name="Hide camera preview").click()
        await expect(canvas).to_be_hidden()
        assert not head._preview_visible and head.scroll_enabled
        await page.goto("https://preview-two.test/")
        await expect(page.get_by_role("button", name="Show camera preview")).to_be_visible()
        await page.reload()
        await expect(page.get_by_role("button", name="Show camera preview")).to_be_visible()
        popup = await context.new_page()
        await popup.goto("https://preview-one.test/new")
        await popup.get_by_role("button", name="Show camera preview").click()
        await expect(popup.locator("canvas")).to_be_visible()
        await popup.get_by_role("button", name="Hide camera preview").focus()
        await popup.keyboard.press("Enter")
        await expect(popup.locator("canvas")).to_be_hidden()
        await popup.get_by_role("button", name="Show camera preview").click()
        head._thread = None
        await expect(popup.get_by_text("Camera unavailable", exact=True)).to_be_visible()
        await expect(popup.locator("canvas")).to_be_hidden()
        await popup.set_viewport_size({"width": 280, "height": 600})
        box = await popup.locator(".camera").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 280
        Path("artifacts").mkdir(exist_ok=True)
        await popup.screenshot(path="artifacts/head-preview.png")
        await browser.close()
    print("PASS: preview pixels, CSP, corner placement, hide/show, keyboard, navigation, reload, new tabs, unavailable camera, narrow viewport")


if __name__ == "__main__":
    asyncio.run(main())
