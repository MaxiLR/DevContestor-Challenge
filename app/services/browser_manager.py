from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright


AA_BOOKING_URL = "https://www.aa.com/booking/choose-flights/1"
_POOL_SIZE = 2


_playwright_manager: Optional[object] = None  # holds the context manager for shutting down cleanly
_playwright: Optional[Playwright] = None
_browser: Optional[Browser] = None
_context: Optional[BrowserContext] = None
_bootstrap_page: Optional[Page] = None
_page_pool: Optional[asyncio.Queue[Page]] = None

_startup_lock = asyncio.Lock()


async def _create_warmed_page() -> Page:
    if not _context:
        raise RuntimeError("Shared Playwright browser context is not initialized.")

    page = await _context.new_page()
    await page.goto(AA_BOOKING_URL)
    await page.wait_for_selector("h1")
    return page


async def _ensure_page_pool() -> None:
    global _bootstrap_page, _page_pool

    if _page_pool is not None:
        return

    queue: asyncio.Queue[Page] = asyncio.Queue()
    # Always keep at least one bootstrap page alive for health checks.
    bootstrap = await _create_warmed_page()
    _bootstrap_page = bootstrap
    await queue.put(bootstrap)

    # Pre-warm additional pages for concurrent use.
    for _ in range(_POOL_SIZE - 1):
        warmed = await _create_warmed_page()
        await queue.put(warmed)

    _page_pool = queue


async def startup_browser() -> None:
    """Start Playwright once and warm the AA booking page."""

    global _playwright_manager, _playwright, _browser, _context

    async with _startup_lock:
        if _browser:
            return

        # Required warm-up sequence executed once during service startup.
        _playwright_manager = async_playwright()
        _playwright = await _playwright_manager.start()
        _browser = await _playwright.firefox.launch(headless=True)
        _context = await _browser.new_context()
        await _ensure_page_pool()


async def shutdown_browser() -> None:
    """Close Playwright resources if they were started."""

    global _playwright_manager, _playwright, _browser, _context, _bootstrap_page, _page_pool

    async with _startup_lock:
        if _page_pool:
            queue = _page_pool
            _page_pool = None
            while True:
                try:
                    page = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                if not page.is_closed():
                    await page.close()

        if _bootstrap_page and not _bootstrap_page.is_closed():
            await _bootstrap_page.close()
        _bootstrap_page = None

        if _context:
            await _context.close()
            _context = None

        if _browser:
            browser = _browser
            _browser = None
            await browser.close()

        if _playwright_manager:
            await _playwright_manager.stop()
            _playwright_manager = None
            _playwright = None


async def get_bootstrap_page() -> Page:
    await ensure_browser_started()

    async with _startup_lock:
        page = _bootstrap_page
        if not page or page.is_closed():
            page = await _create_warmed_page()
            _bootstrap_page = page
            if _page_pool:
                await _page_pool.put(page)
        return page


def get_browser() -> Browser:
    if not _browser:
        raise RuntimeError("Shared Playwright browser is not initialized.")
    return _browser


async def ensure_browser_started() -> None:
    if not _browser or not _context:
        await startup_browser()


@asynccontextmanager
async def acquire_page() -> AsyncIterator[Page]:
    await ensure_browser_started()

    async with _startup_lock:
        queue = _page_pool

    if not queue:
        raise RuntimeError("Playwright page pool is not initialized.")

    page = await queue.get()
    try:
        if page.is_closed():
            page = await _create_warmed_page()
        elif page.url != AA_BOOKING_URL:
            await page.goto(AA_BOOKING_URL)

        yield page
    finally:
        # Return the page to the pool for reuse if still available.
        async with _startup_lock:
            queue = _page_pool

        if queue and not page.is_closed():
            await queue.put(page)
