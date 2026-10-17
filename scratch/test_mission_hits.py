from playwright.sync_api import sync_playwright
from pathlib import Path

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(Path("ui/home.html").resolve().as_uri())
    page.wait_for_load_state("networkidle")

    # Scroll from top of mission to bottom in 50px increments
    results = []
    for scroll_y in range(500, 2500, 100):
        page.evaluate(f"window.scrollTo(0, {scroll_y})")
        page.wait_for_timeout(100)
        
        info = page.evaluate("""() => {
            const card = document.querySelector('.cloud-statement-inner');
            const r = card.getBoundingClientRect();
            if (r.bottom <= 0 || r.top >= window.innerHeight) return null;
            
            // Sample 9 points across the card
            const points = [
                { name: 'top-left', x: r.left + 20, y: r.top + 20 },
                { name: 'top-right', x: r.right - 20, y: r.top + 20 },
                { name: 'center', x: r.left + r.width / 2, y: r.top + r.height / 2 },
                { name: 'bottom-left', x: r.left + 20, y: r.bottom - 20 },
                { name: 'bottom-right', x: r.right - 20, y: r.bottom - 20 }
            ];
            
            const hits = points.map(p => {
                const el = document.elementFromPoint(p.x, p.y);
                const isCardOrChild = el === card || card.contains(el);
                return {
                    name: p.name,
                    x: p.x,
                    y: p.y,
                    isCardOrChild,
                    hitTag: el ? el.tagName : null,
                    hitClass: el ? el.className : null
                };
            });
            
            return {
                scrollY: window.scrollY,
                hits
            };
        }""")
        if info:
            results.append(info)

    import pprint
    for res in results:
        not_card = [h for h in res['hits'] if not h['isCardOrChild']]
        if not_card:
            print(f"AT SCROLL {res['scrollY']}, ELEMENTS ON TOP OF CARD:")
            pprint.pprint(not_card)
        else:
            print(f"AT SCROLL {res['scrollY']}: All card points hit card or children.")

    browser.close()
