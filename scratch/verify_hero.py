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
            page.wait_for_timeout(800)
            
            if th == 'dark':
                page.evaluate('document.documentElement.setAttribute("data-theme", "dark")')
                page.wait_for_timeout(300)
                
            # Check horizontal overflow
            overflow = page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
            print(f'[{vp_name} - {th}] Horizontal overflow:', overflow)
            
            # Check bounding boxes of logo, headline, and search form
            logo = page.locator('.hero-logo')
            title = page.locator('.hero-title')
            search = page.locator('.hero-search')
            
            lbox = logo.bounding_box()
            tbox = title.bounding_box()
            sbox = search.bounding_box()
            
            print(f'[{vp_name} - {th}] Logo box: y={lbox["y"]:.1f}, h={lbox["height"]:.1f}, w={lbox["width"]:.1f}')
            print(f'[{vp_name} - {th}] Title box: y={tbox["y"]:.1f}, h={tbox["height"]:.1f}, w={tbox["width"]:.1f}')
            print(f'[{vp_name} - {th}] Search box: y={sbox["y"]:.1f}, h={sbox["height"]:.1f}, w={sbox["width"]:.1f}')
            
            gap_logo_title = tbox['y'] - (lbox['y'] + lbox['height'])
            gap_title_search = sbox['y'] - (tbox['y'] + tbox['height'])
            print(f'[{vp_name} - {th}] Gap Logo->Title: {gap_logo_title:.1f}px | Gap Title->Search: {gap_title_search:.1f}px')
            
            # Screenshot hero
            page.screenshot(path=f'scratch/hero_{vp_name}_{th}.png')
            
            # Check elementFromPoint over logo center, title center, search input center
            logo_center_tag = page.evaluate('([x, y]) => document.elementFromPoint(x, y)?.tagName', [lbox['x'] + lbox['width']/2, lbox['y'] + lbox['height']/2])
            title_center_tag = page.evaluate('([x, y]) => document.elementFromPoint(x, y)?.tagName', [tbox['x'] + tbox['width']/2, tbox['y'] + tbox['height']/2])
            search_input_tag = page.evaluate('([x, y]) => document.elementFromPoint(x, y)?.tagName', [sbox['x'] + sbox['width']/2, sbox['y'] + sbox['height']/2])
            
            print(f'[{vp_name} - {th}] Hit tags: Logo={logo_center_tag}, Title={title_center_tag}, Search={search_input_tag}')
            
            page.close()
            
    browser.close()
print('Hero verification complete.')
