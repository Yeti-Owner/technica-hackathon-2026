import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def verify():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        # Test desktop light & dark
        for theme in ["light", "dark"]:
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            file_url = (Path("ui/home.html").resolve()).as_uri()
            await page.goto(file_url)
            if theme == "dark":
                await page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
            
            # Scroll to #features
            features = page.locator("#features")
            await features.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)
            
            card01 = page.locator(".feature-card").nth(0)
            title01 = card01.locator("h3")
            num01 = card01.locator(".card-num")
            desc01 = card01.locator("p")
            footer01 = card01.locator(".card-footer")
            
            title_text = await title01.inner_text()
            desc_text = await desc01.inner_text()
            num_text = await num01.inner_text()
            footer_text = await footer01.inner_text()
            
            print(f"[{theme} Desktop]")
            print(f"  Title: {title_text}")
            print(f"  Num: {num_text}")
            print(f"  Footer: {footer_text}")
            print(f"  Desc: {desc_text}")
            
            # Check bounding boxes of all 4 cards
            cards = page.locator(".feature-card")
            count = await cards.count()
            print(f"  Found {count} cards")
            boxes = []
            for i in range(count):
                c = cards.nth(i)
                box = await c.bounding_box()
                boxes.append(box)
                print(f"  Card {i+1}: height={box['height']}, y={box['y']}, width={box['width']}")
                
            # Screenshot features section
            await page.screenshot(path=f"scratch/card01_desktop_{theme}.png")
            await page.close()
            
        # Test Tablet
        page = await browser.new_page(viewport={"width": 768, "height": 1024})
        await page.goto(file_url)
        await page.locator("#features").scroll_into_view_if_needed()
        await page.wait_for_timeout(500)
        await page.screenshot(path="scratch/card01_tablet.png")
        await page.close()

        # Test Mobile
        page = await browser.new_page(viewport={"width": 375, "height": 812})
        await page.goto(file_url)
        await page.locator("#features").scroll_into_view_if_needed()
        await page.wait_for_timeout(500)
        await page.screenshot(path="scratch/card01_mobile.png")
        await page.close()

        await browser.close()

if __name__ == "__main__":
    asyncio.run(verify())
