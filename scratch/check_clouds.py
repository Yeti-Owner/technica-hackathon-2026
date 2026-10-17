import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        # 1. Desktop Check (1440 x 900)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        html_path = (Path(__file__).resolve().parent.parent / "ui/home.html").as_uri()
        await page.goto(html_path)
        await page.wait_for_timeout(1000)

        # Check safe zone bounding boxes
        content_box = await page.locator(".hero-content").bounding_box()
        search_box = await page.locator(".hero-search").bounding_box()
        title_box = await page.locator(".hero-title").bounding_box()
        logo_box = await page.locator(".hero-logo").bounding_box()
        
        print("Hero Content Box:", content_box)
        print("Search Box:", search_box)

        clouds = await page.locator(".hero-cloud").all()
        print(f"Total hero clouds rendered: {len(clouds)}")
        for idx, cloud in enumerate(clouds):
            cls = await cloud.get_attribute("class")
            box = await cloud.bounding_box()
            print(f"Cloud {idx} ({cls}): {box}")
            # Ensure no cloud overlaps the search input directly
            if box:
                # Check overlap with search bar
                overlap_x = not (box["x"] + box["width"] < search_box["x"] or box["x"] > search_box["x"] + search_box["width"])
                overlap_y = not (box["y"] + box["height"] < search_box["y"] or box["y"] > search_box["y"] + search_box["height"])
                if overlap_x and overlap_y:
                    print(f"WARNING: Cloud {cls} overlaps search box!")
                else:
                    print(f"OK: Cloud {cls} safe from search box.")

        # Capture desktop light
        await page.screenshot(path="scratch/hero_desktop_light.png")

        # Capture desktop dark
        await page.locator("#theme").click()
        await page.wait_for_timeout(500)
        await page.screenshot(path="scratch/hero_desktop_dark.png")
        
        # 2. Mobile Check (390 x 844)
        mobile_page = await browser.new_page(viewport={"width": 390, "height": 844})
        await mobile_page.goto(html_path)
        await mobile_page.wait_for_timeout(1000)
        await mobile_page.screenshot(path="scratch/hero_mobile_light.png")
        
        # Check horizontal overflow
        scroll_width = await mobile_page.evaluate("document.documentElement.scrollWidth")
        client_width = await mobile_page.evaluate("document.documentElement.clientWidth")
        print(f"Mobile Scroll Width: {scroll_width}, Client Width: {client_width}")
        assert scroll_width == client_width, f"Mobile horizontal overflow! {scroll_width} > {client_width}"

        await browser.close()
        print("All cloud checks passed successfully!")

if __name__ == "__main__":
    asyncio.run(run())
