from playwright.sync_api import sync_playwright

viewports = [
    ('desktop', 1440, 900),
    ('tablet', 768, 1024),
    ('mobile', 375, 812),
]

themes = ['light', 'dark']

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    
    for vp_name, w, h in viewports:
        for th in themes:
            page = browser.new_page(viewport={'width': w, 'height': h})
            page.goto('file:///C:/Tomi%20Apps/technica-hackathon-2026/ui/home.html')
            page.wait_for_timeout(600)
            
            if th == 'dark':
                page.evaluate('document.documentElement.setAttribute("data-theme", "dark")')
                page.wait_for_timeout(300)
                
            # Check horizontal overflow on document
            overflow = page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
            print(f'[{vp_name} - {th}] Horizontal overflow:', overflow)
            
            # Mission section screenshot
            mission = page.locator('#mission')
            mission.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            page.screenshot(path=f'scratch/verify_{vp_name}_{th}_mission.png')
            
            # Features section screenshot
            features = page.locator('#features')
            features.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            page.screenshot(path=f'scratch/verify_{vp_name}_{th}_features.png')
            
            # Check element stacking at mission card center & edges
            card_hits = page.evaluate('''() => {
                const card = document.querySelector('.cloud-statement-inner');
                const rect = card.getBoundingClientRect();
                const pts = [
                    { x: rect.left + 40, y: rect.top + 40 },
                    { x: rect.right - 40, y: rect.top + 40 },
                    { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 },
                    { x: rect.left + 40, y: rect.bottom - 40 },
                    { x: rect.right - 40, y: rect.bottom - 40 },
                ];
                return pts.map(p => {
                    const el = document.elementFromPoint(p.x, p.y);
                    return { inside: card.contains(el), tag: el ? el.tagName : null, cls: el ? el.className : null };
                });
            }''')
            print(f'[{vp_name} - {th}] Mission card points all inside card:', all(h['inside'] for h in card_hits))
            
            # Check feature cards visibility
            cards_count = page.locator('.feature-card').count()
            print(f'[{vp_name} - {th}] Feature cards count: {cards_count}')
            
            page.close()
            
    browser.close()
print('All verifications complete successfully.')
