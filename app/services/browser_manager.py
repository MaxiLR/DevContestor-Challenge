from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
    TimeoutError,
)

from app.core.exceptions import BrowserFingerprintBannedException


AA_HOMEPAGE_URL = "https://www.aa.com/"
AA_BOOKING_URL = "https://www.aa.com/booking"  # Used for referer header in API requests
AA_WARMUP_SELECTOR = '[id="flightSearchForm.button.reSubmit"]'
AA_WARMUP_TIMEOUT = 10000  # 10 seconds
_POOL_SIZE = 2

SearchType = Literal["Award", "Revenue"]

# Award browser globals
_award_playwright_manager: Optional[object] = None
_award_playwright: Optional[Playwright] = None
_award_browser: Optional[Browser] = None
_award_context: Optional[BrowserContext] = None
_award_bootstrap_page: Optional[Page] = None
_award_page_pool: Optional[asyncio.Queue[Page]] = None

# Cash (Revenue) browser globals
_cash_playwright_manager: Optional[object] = None
_cash_playwright: Optional[Playwright] = None
_cash_browser: Optional[Browser] = None
_cash_context: Optional[BrowserContext] = None
_cash_bootstrap_page: Optional[Page] = None
_cash_page_pool: Optional[asyncio.Queue[Page]] = None

_startup_lock = asyncio.Lock()


async def _create_warmed_page(search_type: SearchType) -> Page:
    """Create a new warmed page for the specified search type (Award or Revenue)."""

    if search_type == "Award":
        context = _award_context
    else:
        context = _cash_context

    if not context:
        raise RuntimeError(f"{search_type} browser context is not initialized.")

    page = await context.new_page()

    try:
        await page.goto(AA_HOMEPAGE_URL, wait_until="domcontentloaded")
        await page.wait_for_selector(AA_WARMUP_SELECTOR, timeout=AA_WARMUP_TIMEOUT)
    except TimeoutError as e:
        await page.close()
        raise BrowserFingerprintBannedException(
            f"Failed to load AA homepage selector '{AA_WARMUP_SELECTOR}' within {AA_WARMUP_TIMEOUT}ms. "
            "This likely indicates the current browser fingerprint was blocked; recycle the context, rotate fingerprints, or route through a different proxy."
        ) from e

    return page


async def _ensure_page_pool(search_type: SearchType) -> None:
    """Ensure the page pool for the specified search type is initialized."""

    global _award_bootstrap_page, _award_page_pool, _cash_bootstrap_page, _cash_page_pool

    if search_type == "Award":
        if _award_page_pool is not None:
            return

        queue: asyncio.Queue[Page] = asyncio.Queue()
        bootstrap = await _create_warmed_page(search_type)
        _award_bootstrap_page = bootstrap
        await queue.put(bootstrap)

        for _ in range(_POOL_SIZE - 1):
            warmed = await _create_warmed_page(search_type)
            await queue.put(warmed)

        _award_page_pool = queue
    else:
        if _cash_page_pool is not None:
            return

        queue: asyncio.Queue[Page] = asyncio.Queue()
        bootstrap = await _create_warmed_page(search_type)
        _cash_bootstrap_page = bootstrap
        await queue.put(bootstrap)

        for _ in range(_POOL_SIZE - 1):
            warmed = await _create_warmed_page(search_type)
            await queue.put(warmed)

        _cash_page_pool = queue


async def startup_browser() -> None:
    """Start both Award and Cash WebKit browsers with warmed page pools."""

    global _award_playwright_manager, _award_playwright, _award_browser, _award_context
    global _cash_playwright_manager, _cash_playwright, _cash_browser, _cash_context

    async with _startup_lock:
        if _award_browser and _cash_browser:
            return

        # Initialize Award browser
        _award_playwright_manager = async_playwright()
        _award_playwright = await _award_playwright_manager.start()
        _award_browser = await _award_playwright.webkit.launch(headless=True)
        _award_context = await _award_browser.new_context()

        # Initialize Cash browser
        _cash_playwright_manager = async_playwright()
        _cash_playwright = await _cash_playwright_manager.start()
        _cash_browser = await _cash_playwright.webkit.launch(headless=True)
        _cash_context = await _cash_browser.new_context()

        # Warm up both page pools concurrently
        await asyncio.gather(
            _ensure_page_pool("Award"),
            _ensure_page_pool("Revenue"),
        )


async def shutdown_browser() -> None:
    """Close both Award and Cash browser resources."""

    global _award_playwright_manager, _award_playwright, _award_browser, _award_context
    global _award_bootstrap_page, _award_page_pool
    global _cash_playwright_manager, _cash_playwright, _cash_browser, _cash_context
    global _cash_bootstrap_page, _cash_page_pool

    async with _startup_lock:
        # Clean up Award browser
        if _award_page_pool:
            queue = _award_page_pool
            _award_page_pool = None
            while True:
                try:
                    page = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if not page.is_closed():
                    await page.close()

        if _award_bootstrap_page and not _award_bootstrap_page.is_closed():
            await _award_bootstrap_page.close()
        _award_bootstrap_page = None

        if _award_context:
            await _award_context.close()
            _award_context = None

        if _award_browser:
            await _award_browser.close()
            _award_browser = None

        if _award_playwright_manager:
            await _award_playwright_manager.stop()
            _award_playwright_manager = None
            _award_playwright = None

        # Clean up Cash browser
        if _cash_page_pool:
            queue = _cash_page_pool
            _cash_page_pool = None
            while True:
                try:
                    page = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if not page.is_closed():
                    await page.close()

        if _cash_bootstrap_page and not _cash_bootstrap_page.is_closed():
            await _cash_bootstrap_page.close()
        _cash_bootstrap_page = None

        if _cash_context:
            await _cash_context.close()
            _cash_context = None

        if _cash_browser:
            await _cash_browser.close()
            _cash_browser = None

        if _cash_playwright_manager:
            await _cash_playwright_manager.stop()
            _cash_playwright_manager = None
            _cash_playwright = None


async def get_bootstrap_page(search_type: SearchType = "Award") -> Page:
    """Get the bootstrap page for the specified search type."""

    global _award_bootstrap_page, _cash_bootstrap_page

    await ensure_browser_started()

    async with _startup_lock:
        if search_type == "Award":
            page = _award_bootstrap_page
            if not page or page.is_closed():
                page = await _create_warmed_page(search_type)
                _award_bootstrap_page = page
                if _award_page_pool:
                    await _award_page_pool.put(page)
            return page
        else:
            page = _cash_bootstrap_page
            if not page or page.is_closed():
                page = await _create_warmed_page(search_type)
                _cash_bootstrap_page = page
                if _cash_page_pool:
                    await _cash_page_pool.put(page)
            return page


def get_browser(search_type: SearchType = "Award") -> Browser:
    """Get the browser instance for the specified search type."""

    if search_type == "Award":
        if not _award_browser:
            raise RuntimeError("Award browser is not initialized.")
        return _award_browser
    else:
        if not _cash_browser:
            raise RuntimeError("Cash browser is not initialized.")
        return _cash_browser


async def ensure_browser_started() -> None:
    """Ensure both browsers are started."""

    if (
        not _award_browser
        or not _award_context
        or not _cash_browser
        or not _cash_context
    ):
        await startup_browser()


@asynccontextmanager
async def acquire_page(search_type: SearchType) -> AsyncIterator[Page]:
    """Acquire a page from the pool for the specified search type (Award or Revenue)."""

    await ensure_browser_started()

    async with _startup_lock:
        queue = _award_page_pool if search_type == "Award" else _cash_page_pool

    if not queue:
        raise RuntimeError(f"{search_type} page pool is not initialized.")

    page = await queue.get()
    try:
        # Only recreate the page if it's closed - no need to re-navigate
        if page.is_closed():
            page = await _create_warmed_page(search_type)

        yield page
    finally:
        # Return the page to the pool for reuse if still available.
        async with _startup_lock:
            queue = _award_page_pool if search_type == "Award" else _cash_page_pool

        if queue and not page.is_closed():
            await queue.put(page)
