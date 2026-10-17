from playwright.sync_api import sync_playwright

viewports = [
    ('desktop-1440', 1440, 900),
    ('desktop-1920', 1920, 1080),
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
                
            metrics = page.evaluate('''() => {
                const logo = document.querySelector('.hero-logo');
                const title = document.querySelector('.hero-title');
                const search = document.querySelector('.hero-search');
                const header = document.querySelector('.site-header');
                
                const lbox = logo.getBoundingClientRect();
                const tbox = title.getBoundingClientRect();
                const sbox = search.getBoundingClientRect();
                const hbox = header.getBoundingClientRect();
                
                // Artwork in 2000x2000 PNG: minY = 507, maxY = 1216
                const visibleLogoTop = lbox.top + lbox.height * (507 / 2000);
                const visibleLogoBottom = lbox.top + lbox.height * (1216 / 2000);
                const visibleLogoHeight = visibleLogoBottom - visibleLogoTop;
                
                return {
                    navbarBottom: hbox.bottom,
                    visibleLogoTop,
                    visibleLogoBottom,
                    visibleLogoHeight,
                    gapNavbarToLogo: visibleLogoTop - hbox.bottom,
                    titleTop: tbox.top,
                    titleBottom: tbox.bottom,
                    gapLogoToTitle: tbox.top - visibleLogoBottom,
                    searchTop: sbox.top,
                    searchBottom: sbox.bottom,
                    gapTitleToSearch: sbox.top - tbox.bottom,
                    viewportHeight: window.innerHeight,
                    spaceBelowSearch: window.innerHeight - sbox.bottom,
                    overflow: document.documentElement.scrollWidth > window.innerWidth
                };
            }''')
            
            print(f'[{vp_name} - {th}] Gap Navbar->Logo: {metrics["gapNavbarToLogo"]:.1f}px | Logo Artwork H: {metrics["visibleLogoHeight"]:.1f}px | Gap Logo->Title: {metrics["gapLogoToTitle"]:.1f}px | Gap Title->Search: {metrics["gapTitleToSearch"]:.1f}px | Below Search: {metrics["spaceBelowSearch"]:.1f}px | Overflow: {metrics["overflow"]}')
            
            if vp_name in ['desktop-1440', 'mobile'] and th == 'light':
                page.screenshot(path=f'scratch/hero_final_{vp_name}.png')
                
            page.close()
            
    browser.close()
print('Metrics verification complete.')
