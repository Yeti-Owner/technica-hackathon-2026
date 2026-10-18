import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def verify():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        url = (Path("ui/image-lab.html").resolve()).as_uri()

        # Desktop Light
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(url)
        # Mock an AI detection on card 1 and 2 to verify white cloud frames
        await page.evaluate("""() => {
            const cards = document.querySelectorAll('.lab-card');
            if (cards[0]) {
                cards[0].classList.add('is-ai-card');
                cards[0].dataset.aiScore = '94';
                const b = cards[0].querySelector('.ai-cloud-badge');
                if (b) b.textContent = 'AI GENERATED · 94';
            }
            if (cards[1]) {
                cards[1].classList.add('is-ai-card');
                cards[1].dataset.aiScore = '88';
                const b = cards[1].querySelector('.ai-cloud-badge');
                if (b) b.textContent = 'AI GENERATED · 88';
            }
        }""")
        await page.wait_for_timeout(400)
        await page.screenshot(path="scratch/lab_desktop_light.png")

        # Desktop Dark
        await page.evaluate("document.documentElement.dataset.theme = 'dark'")
        await page.wait_for_timeout(300)
        await page.screenshot(path="scratch/lab_desktop_dark.png")
        await page.close()

        # Mobile Light
        page_m = await browser.new_page(viewport={"width": 390, "height": 844})
        await page_m.goto(url)
        await page_m.evaluate("""() => {
            const cards = document.querySelectorAll('.lab-card');
            if (cards[0]) {
                cards[0].classList.add('is-ai-card');
                cards[0].dataset.aiScore = '94';
                const b = cards[0].querySelector('.ai-cloud-badge');
                if (b) b.textContent = 'AI GENERATED · 94';
            }
        }""")
        await page_m.wait_for_timeout(400)
        await page_m.screenshot(path="scratch/lab_mobile_light.png")
        await page_m.close()

        # Test Lightbox Modal
        page_lb = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page_lb.goto(url)
        await page_lb.evaluate("""() => {
            const cards = document.querySelectorAll('.lab-card');
            if (cards[0]) {
                cards[0].classList.add('is-ai-card');
                cards[0].dataset.aiScore = '94';
                cards[0].click();
            }
        }""")
        await page_lb.wait_for_timeout(400)
        await page_lb.screenshot(path="scratch/lab_lightbox.png")
        await page_lb.close()

        await browser.close()
        print("Screenshots taken successfully!")

if __name__ == "__main__":
    asyncio.run(verify())
