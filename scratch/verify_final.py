import asyncio
from playwright.async_api import async_playwright
import os

async def capture():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        
        file_url = "file:///" + os.path.abspath("ui/home.html").replace("\\", "/")
        await page.goto(file_url)
        await page.wait_for_timeout(1000)
        
        # 1. Desktop Light: Rest state (no active highlights, enlarged logo)
        await page.screenshot(path="scratch/check_desktop_light.png")
        print("Captured scratch/check_desktop_light.png")

        # 2. Desktop Mouse Click: Click on 'Mission' link (should NOT leave blue ring or dot)
        mission_link = page.locator(".header-nav a[href='#mission']")
        await mission_link.click()
        await page.wait_for_timeout(500)
        await page.screenshot(path="scratch/check_desktop_mouse_click.png")
        print("Captured scratch/check_desktop_mouse_click.png")

        # 3. Desktop Keyboard Focus: Press Tab to focus 'Features' link
        # Re-navigate to top
        await page.goto(file_url)
        await page.wait_for_timeout(500)
        await page.keyboard.press("Tab") # Skip link
        await page.keyboard.press("Tab") # Logo cloud
        await page.keyboard.press("Tab") # Features cloud
        await page.screenshot(path="scratch/check_desktop_keyboard_focus.png")
        print("Captured scratch/check_desktop_keyboard_focus.png")

        # 4. Desktop Dark Theme
        theme_btn = page.locator("#theme")
        await theme_btn.click()
        await page.wait_for_timeout(800)
        await page.screenshot(path="scratch/check_desktop_dark.png")
        print("Captured scratch/check_desktop_dark.png")

        # 5. Mobile Viewport (375x700)
        await page.set_viewport_size({"width": 375, "height": 700})
        await page.screenshot(path="scratch/check_mobile_dark.png")
        print("Captured scratch/check_mobile_dark.png")

        # Mobile Light
        await theme_btn.click()
        await page.wait_for_timeout(500)
        await page.screenshot(path="scratch/check_mobile_light.png")
        print("Captured scratch/check_mobile_light.png")

        await browser.close()

asyncio.run(capture())
