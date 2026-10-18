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

    # Let's inspect all elements inside or overlapping .cloud-transition-section
    info = page.evaluate("""() => {
        const section = document.querySelector('#mission');
        const card = document.querySelector('.cloud-statement-inner');
        const cardRect = card.getBoundingClientRect();
        
        // Find all elements whose bounding rect overlaps cardRect
        const allEls = Array.from(document.querySelectorAll('*'));
        const overlapping = allEls.filter(el => {
            if (el === card || card.contains(el) || el === document.body || el === document.documentElement || el === document.querySelector('main') || el === section) return false;
            const r = el.getBoundingClientRect();
            return (
                r.left < cardRect.right &&
                r.right > cardRect.left &&
                r.top < cardRect.bottom &&
                r.bottom > cardRect.top &&
                r.width > 0 && r.height > 0
            );
        }).map(el => {
            const cs = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return {
                tagName: el.tagName,
                className: el.className,
                src: el.src,
                zIndex: cs.zIndex,
                position: cs.position,
                rect: { top: r.top, left: r.left, width: r.width, height: r.height }
            };
        });
        
        return {
            cardRect,
            cardZIndex: getComputedStyle(card).zIndex,
            cardTransform: getComputedStyle(card).transform,
            overlapping
        };
    }""")
    import pprint
    pprint.pprint(info)
    browser.close()
