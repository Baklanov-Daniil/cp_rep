import asyncio
from playwright.async_api import async_playwright

async def test_browser():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        print("🌐 Открываем Google...")
        await page.goto("https://google.com")
        print("✅ Успех!")
        
        await asyncio.sleep(3)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_browser())