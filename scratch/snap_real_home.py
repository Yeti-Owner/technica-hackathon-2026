import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        # 1. Desktop Light (1440x900)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        html_path = (Path("ui/home.html").resolve()).as_uri()
        await page.goto(html_path)
        await page.wait_for_timeout(1000)
        await page.screenshot(path="scratch/home_cloud_search_desktop_light.png")

        # Focus state in search bar
        await page.locator("#query").focus()
        await page.wait_for_timeout(400)
        await page.screenshot(path="scratch/home_cloud_search_focus_light.png")

        # Dark theme
        await page.locator("#theme").click()
        await page.wait_for_timeout(500)
        await page.screenshot(path="scratch/home_cloud_search_desktop_dark.png")

        # Focus state in dark mode
        await page.locator("#query").focus()
        await page.wait_for_timeout(400)
        await page.screenshot(path="scratch/home_cloud_search_focus_dark.png")

        # 2. Mobile View (390x844)
        mobile_page = await browser.new_page(viewport={"width": 390, "height": 844})
        await mobile_page.goto(html_path)
        await mobile_page.wait_for_timeout(1000)
        await mobile_page.screenshot(path="scratch/home_cloud_search_mobile_light.png")

        # Mobile Dark
        await mobile_page.locator("#theme").click()
        await mobile_page.wait_for_timeout(500)
        await mobile_page.screenshot(path="scratch/home_cloud_search_mobile_dark.png")

        # Check mobile scroll width
        scroll_width = await mobile_page.evaluate("document.documentElement.scrollWidth")
        client_width = await mobile_page.evaluate("document.documentElement.clientWidth")
        print(f"Mobile Scroll: {scroll_width}, Client: {client_width}")
        assert scroll_width == client_width, "Mobile overflow detected!"

        await browser.close()
        print("All screenshots captured and verified successfully!")

if __name__ == "__main__":
    asyncio.run(run())
