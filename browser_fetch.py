"""Bounded anonymous Chromium fallback for public Gumtree search pages."""
from urllib.parse import urlsplit


class PublicSearchBrowser:
    def __init__(self):
        self.playwright = self.browser = self.context = None
        self.unavailable = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def fetch(self, url: str) -> tuple[int, str]:
        if urlsplit(url).hostname not in ('www.gumtree.com', 'gumtree.com'):
            raise ValueError('Unsupported public search host')
        if self.unavailable:
            raise RuntimeError('Browser unavailable')
        if not self.context:
            try:
                from playwright.async_api import async_playwright
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(headless=True, timeout=20000,
                    args=['--no-sandbox', '--disable-dev-shm-usage'])
                self.context = await self.browser.new_context(locale='en-GB')
                async def lightweight(route):
                    if route.request.resource_type in ('image', 'media', 'font'):
                        await route.abort()
                    else:
                        await route.continue_()
                await self.context.route('**/*', lightweight)
            except Exception:
                self.unavailable = True
                raise
        page = await self.context.new_page()
        try:
            response = await page.goto(url, wait_until='domcontentloaded', timeout=25000)
            return (response.status if response else 0), await page.content()
        finally:
            await page.close()
