from playwright.sync_api import sync_playwright
from pathlib import Path

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(Path("ui/home.html").resolve().as_uri())
    page.wait_for_load_state("networkidle")

    # Scroll to mission
    page.locator('.header-nav a[href="#mission"]').dispatch_event("click")
    page.wait_for_timeout(1500)
    page.screenshot(path="scratch/mission_scroll.png")

    # Check elements at card center and top-right
    mission_card = page.locator(".cloud-statement-inner")
    box = mission_card.bounding_box()
    print("Mission card box:", box)

    # Check what element is at top right of mission card:
    top_right_x = box["x"] + box["width"] - 20
    top_right_y = box["y"] + 20
    el_at_top_right = page.evaluate(f"() => document.elementFromPoint({top_right_x}, {top_right_y})?.className")
    print("Element at mission card top right:", el_at_top_right)

    # Scroll into features / horizontal section
    page.locator('.header-nav a[href="#features"]').dispatch_event("click")
    page.wait_for_timeout(1500)
    page.screenshot(path="scratch/features_start.png")

    # Scroll horizontal by wheeling or scrolling
    page.mouse.wheel(0, 800)
    page.wait_for_timeout(1000)
    page.screenshot(path="scratch/features_mid.png")

    page.mouse.wheel(0, 1500)
    page.wait_for_timeout(1000)
    page.screenshot(path="scratch/features_end.png")

    browser.close()
