from __future__ import annotations

import asyncio
from typing import Dict, Optional

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright


AA_BOOKING_URL = "https://www.aa.com/booking/choose-flights/1"


_playwright_manager: Optional[object] = None  # holds the context manager for shutting down cleanly
_playwright: Optional[Playwright] = None
_browser: Optional[Browser] = None
_context: Optional[BrowserContext] = None

_startup_lock = asyncio.Lock()


async def _warm_context(context: BrowserContext) -> Dict[str, object]:
    page = await context.new_page()
    try:
        await page.goto(AA_BOOKING_URL)
        await page.wait_for_selector("h1")
        user_agent = await page.evaluate("() => navigator.userAgent")
        language = await page.evaluate("() => navigator.language")
        languages = await page.evaluate("() => navigator.languages")
    finally:
        await page.close()

    return {
        "user_agent": user_agent,
        "language": language,
        "languages": languages,
    }


async def _replace_context_locked() -> BrowserContext:
    global _context

    if not _browser:
        raise RuntimeError("Shared Playwright browser is not initialized.")

    if _context:
        await _context.close()

    _context = await _browser.new_context()
    return _context


async def startup_browser() -> None:
    """Start Playwright once and warm the AA booking page."""

    global _playwright_manager, _playwright, _browser, _context

    async with _startup_lock:
        if not _browser:
            _playwright_manager = async_playwright()
            _playwright = await _playwright_manager.start()
            _browser = await _playwright.firefox.launch(headless=True)

        if not _context:
            context = await _browser.new_context()
            _context = context
        else:
            context = _context

    await _warm_context(context)


async def shutdown_browser() -> None:
    """Close Playwright resources if they were started."""

    global _playwright_manager, _playwright, _browser, _context

    async with _startup_lock:
        if _context:
            await _context.close()
            _context = None

        if _browser:
            browser = _browser
            _browser = None
            await browser.close()

        if _playwright_manager:
            await _playwright_manager.__aexit__(None, None, None)
            _playwright_manager = None
            _playwright = None


async def ensure_browser_started() -> None:
    if not _browser or not _context:
        await startup_browser()


def get_browser_context() -> BrowserContext:
    if not _context:
        raise RuntimeError("Shared Playwright browser context is not initialized.")
    return _context


async def refresh_browser_session() -> Dict[str, object]:
    await ensure_browser_started()

    async with _startup_lock:
        context = await _replace_context_locked()

    warm_info = await _warm_context(context)
    cookies = await context.cookies()
    return {"cookies": cookies, **warm_info}
