from playwright.sync_api import sync_playwright
from pathlib import Path

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(Path("ui/home.html").resolve().as_uri())
    page.wait_for_load_state("networkidle")

    # Scroll to features
    page.locator('.header-nav a[href="#features"]').dispatch_event("click")
    page.wait_for_timeout(1000)
    page.mouse.wheel(0, 800)
    page.wait_for_timeout(1000)

    # Let's inspect all elements with 'cloud' in class name and their bounding boxes and z-indexes
    clouds = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('[class*="cloud"]')).map(el => {
            const r = el.getBoundingClientRect();
            const cs = getComputedStyle(el);
            return {
                tagName: el.tagName,
                className: el.className,
                src: el.src || (el.querySelector('img') ? el.querySelector('img').src : null),
                rect: { top: r.top, left: r.left, width: r.width, height: r.height },
                zIndex: cs.zIndex,
                position: cs.position,
                opacity: cs.opacity
            };
        }).filter(c => c.rect.width > 0 && c.rect.height > 0 && c.rect.top < window.innerHeight && c.rect.top + c.rect.height > 0);
    }""")
    import pprint
    print("Visible clouds in viewport:")
    pprint.pprint(clouds)

    browser.close()
