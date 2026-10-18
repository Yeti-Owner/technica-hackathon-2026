"""Offline integration check. Opens a visible browser and closes it afterwards."""
import asyncio
from pathlib import Path

from playwright.async_api import expect
from browserkit import BrowserController
from browserkit.controller import ROOT, lab_urls


async def main():
    async with BrowserController() as b:
        await expect(b.page.get_by_role("heading", name="Where to next?")).to_be_visible()
        await b.page.get_by_role("button", name="Dark theme").click()
        await expect(b.page.locator("html")).to_have_attribute("data-theme", "dark")
        Path("artifacts").mkdir(exist_ok=True)
        await b.page.screenshot(path="artifacts/home.png")

        # The redesigned home's closing button opens the AI image lab.
        await b.click_link("Try Practice Tools")
        await expect(b.page.get_by_role("heading", name="A closer look at the pictures you see.", exact=True)).to_be_visible()
        await expect(b.page.locator("#lab-grid")).to_have_count(1)
        await expect(b.page.get_by_role("link", name="Home", exact=True)).to_be_visible()
        expected_images = lab_urls((ROOT / "test_images.txt").read_text(encoding="utf-8"))
        await expect(b.page.locator("#lab-grid figure")).to_have_count(len(expected_images))

        # The practice page is UI-orphaned but still reachable by direct URL.
        await b.goto((ROOT / "ui/practice.html").as_uri())
        await expect(b.page.get_by_role("heading", name="Try the controls.")).to_be_visible()
        assert await b.page.locator("#browser-starter-style").count() == 1
        await b.page.get_by_role("link", name="Home", exact=True).focus()
        await b.next_focus()
        await expect(b.page.get_by_role("link", name="Jump to destination")).to_be_focused()
        await b.next_focus()
        await expect(b.page.get_by_role("button", name="Practice button")).to_be_focused()
        await b.previous_focus()
        await expect(b.page.get_by_role("link", name="Jump to destination")).to_be_focused()
        await b.next_focus()
        await b.activate()
        await expect(b.page.get_by_role("button", name="Button activated")).to_be_visible()
        images = await b.images()
        assert images and images[0]["alt"] == "Blue mountain shapes under a yellow sun"
        await b.capture_image(images[0]["index"], "artifacts/test-image.png", images[0]["frame"])
        await b.evaluate("window.scrollTo(0, 0)")
        await b.scroll(500)
        await b.page.wait_for_function("window.scrollY > 0")
        assert "Try the controls." in await b.text()
        assert any(link["text"] == "Return home" for link in await b.links())
        await b.page.reload()
        await expect(b.page.locator("#browser-starter-style")).to_have_count(1)
        popup = await b.context.new_page()
        await popup.goto((Path(__file__).resolve().parent / "ui/home.html").as_uri())
        assert b.page == popup
        await expect(popup.locator("#browser-starter-style")).to_have_count(1)
    print("PASS: home, theme, practice-tools -> image lab link, focus, images, scroll, reading, reload and new-tab injection")


if __name__ == "__main__":
    asyncio.run(main())
