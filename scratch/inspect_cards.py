import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(Path("ui/image-lab.html").resolve().as_uri())
        cards = await page.locator(".lab-card").all()
        for i, c in enumerate(cards[:6]):
            box = await c.bounding_box()
            print(f"Card {i+1}: w={box['width']}, h={box['height']}, x={box['x']}, y={box['y']}")
        grid_box = await page.locator("#lab-grid").bounding_box()
        print(f"Grid: w={grid_box['width']}, h={grid_box['height']}, x={grid_box['x']}, y={grid_box['y']}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(inspect())
