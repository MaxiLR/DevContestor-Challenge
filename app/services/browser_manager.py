from __future__ import annotations

from threading import Lock
from typing import Optional

from playwright.sync_api import Browser, Page, Playwright, sync_playwright


_playwright_manager: Optional[object] = None  # holds the context manager for shutting down cleanly
_playwright: Optional[Playwright] = None
_browser: Optional[Browser] = None
_bootstrap_page: Optional[Page] = None

_startup_lock = Lock()


def _warm_bootstrap_page() -> Page:
    global _bootstrap_page

    if not _browser:
        raise RuntimeError("Shared Playwright browser is not initialized.")

    page = _browser.new_page()
    page.goto("https://www.aa.com/booking/choose-flights/1")
    page.wait_for_selector("h1")
    _bootstrap_page = page
    return page


def startup_browser() -> None:
    """Start Playwright once and warm the AA booking page."""

    global _playwright_manager, _playwright, _browser, _bootstrap_page

    with _startup_lock:
        if _browser:
            return

        # Required warm-up sequence executed once during service startup.
        _playwright_manager = sync_playwright()
        _playwright = _playwright_manager.__enter__()
        _browser = _playwright.firefox.launch(headless=True)
        _warm_bootstrap_page()


def shutdown_browser() -> None:
    """Close Playwright resources if they were started."""

    global _playwright_manager, _playwright, _browser, _bootstrap_page

    with _startup_lock:
        if _bootstrap_page:
            page = _bootstrap_page
            _bootstrap_page = None
            if not page.is_closed():
                page.close()

        if _browser:
            browser = _browser
            _browser = None
            browser.close()

        if _playwright_manager:
            _playwright_manager.__exit__(None, None, None)
            _playwright_manager = None
            _playwright = None


def get_bootstrap_page() -> Page:
    ensure_browser_started()

    with _startup_lock:
        page = _bootstrap_page
        if not page or page.is_closed():
            page = _warm_bootstrap_page()
        return page


def get_browser() -> Browser:
    if not _browser:
        raise RuntimeError("Shared Playwright browser is not initialized.")
    return _browser


def ensure_browser_started() -> None:
    if not _browser:
        startup_browser()
