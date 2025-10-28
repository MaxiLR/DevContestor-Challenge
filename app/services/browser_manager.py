from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError,
    async_playwright,
)


AA_BOOKING_URL = "https://www.aa.com/booking/choose-flights/1"
_POOL_SIZE = 6
_NAV_TIMEOUT_MS = 45_000
_MAX_WARM_ATTEMPTS = 3


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

    last_error: Exception | None = None

    for _ in range(_MAX_WARM_ATTEMPTS):
        page = await _context.new_page()
        try:
            await page.goto(
                AA_BOOKING_URL,
                wait_until="domcontentloaded",
                timeout=_NAV_TIMEOUT_MS,
            )
            await page.wait_for_selector("h1", timeout=_NAV_TIMEOUT_MS)
            return page
        except TimeoutError as exc:
            last_error = exc
        except Exception as exc:  # pragma: no cover - unexpected
            last_error = exc
        try:
            await page.close()
        except Exception:  # pragma: no cover - best effort cleanup
            pass

    raise RuntimeError("Unable to warm AA booking page.") from last_error


async def _ensure_page_pool() -> None:
    global _bootstrap_page, _page_pool

    if _page_pool is not None:
        return

    queue: asyncio.Queue[Page] = asyncio.Queue()

    bootstrap = await _create_warmed_page()
    _bootstrap_page = bootstrap
    await queue.put(bootstrap)

    for _ in range(_POOL_SIZE - 1):
        warmed = await _create_warmed_page()
        await queue.put(warmed)

    _page_pool = queue


async def startup_browser() -> None:
    """Start Playwright once and warm the AA booking page."""

    global _playwright_manager, _playwright, _browser, _context

    async with _startup_lock:
        if _browser and _context:
            return

        if not _browser:
            _playwright_manager = async_playwright()
            _playwright = await _playwright_manager.start()
            _browser = await _playwright.firefox.launch(headless=True)

        if _context:
            await _context.close()

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


async def ensure_browser_started() -> None:
    if not _browser or not _context:
        await startup_browser()


async def _reset_context_locked() -> None:
    global _context, _bootstrap_page, _page_pool

    if not _browser:
        raise RuntimeError("Shared Playwright browser is not initialized.")

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

    _context = await _browser.new_context()
    await _ensure_page_pool()


async def refresh_browser_session() -> None:
    await ensure_browser_started()

    async with _startup_lock:
        await _reset_context_locked()

    # Ensure the pool is available for subsequent callers.
    await ensure_browser_started()


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


async def _return_page_to_pool(page: Page) -> None:
    async with _startup_lock:
        queue = _page_pool

    if not queue:
        return

    if not page.is_closed():
        await queue.put(page)
        return

    try:
        replacement = await _create_warmed_page()
    except Exception:
        return

    if replacement:
        await queue.put(replacement)


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
            try:
                await page.goto(
                    AA_BOOKING_URL,
                    wait_until="domcontentloaded",
                    timeout=_NAV_TIMEOUT_MS,
                )
                await page.wait_for_selector("h1", timeout=_NAV_TIMEOUT_MS)
            except TimeoutError:
                await page.close()
                page = await _create_warmed_page()

        yield page
    finally:
        await asyncio.shield(_return_page_to_pool(page))
