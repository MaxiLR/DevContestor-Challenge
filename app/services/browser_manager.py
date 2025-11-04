from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Literal, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError,
    async_playwright,
)

from app.core.exceptions import BrowserFingerprintBannedException

logger = logging.getLogger(__name__)

AA_HOMEPAGE_URL = "https://www.aa.com/"
AA_BOOKING_URL = "https://www.aa.com/booking"  # Used for referer header in API requests
AA_WARMUP_SELECTOR = '[id="flightSearchForm.button.reSubmit"]'
AA_WARMUP_TIMEOUT = 10000  # 10 seconds
_POOL_SIZE = 1
_ROTATION_THRESHOLD = 75

SearchType = Literal["Award", "Revenue"]
PairKey = Literal["webkit", "firefox"]


@dataclass
class BrowserPairState:
    """Container holding browser resources for a specific engine pair."""

    engine: PairKey
    manager: Optional[object] = None
    playwright: Optional[Playwright] = None
    award_browser: Optional[Browser] = None
    cash_browser: Optional[Browser] = None
    award_context: Optional[BrowserContext] = None
    cash_context: Optional[BrowserContext] = None
    award_page: Optional[Page] = None
    cash_page: Optional[Page] = None
    healthy: bool = False


_browser_pairs: Dict[PairKey, BrowserPairState] = {
    "webkit": BrowserPairState(engine="webkit"),
    "firefox": BrowserPairState(engine="firefox"),
}

_active_pair: PairKey = "webkit"
_request_counter = 0
_startup_lock = asyncio.Lock()
_request_counter_lock = asyncio.Lock()


def _get_pair_state(pair_key: PairKey) -> BrowserPairState:
    return _browser_pairs[pair_key]


async def _teardown_pair(state: BrowserPairState) -> None:
    """Release all Playwright resources for a browser pair."""

    # Close single pages
    for page_attr in ("award_page", "cash_page"):
        page = getattr(state, page_attr)
        if page and not page.is_closed():
            await page.close()
        setattr(state, page_attr, None)

    # Close contexts and browsers
    for context_attr in ("award_context", "cash_context"):
        context = getattr(state, context_attr)
        if context:
            await context.close()
        setattr(state, context_attr, None)

    for browser_attr in ("award_browser", "cash_browser"):
        browser = getattr(state, browser_attr)
        if browser:
            await browser.close()
        setattr(state, browser_attr, None)

    # Stop Playwright manager
    if state.manager:
        try:
            await state.manager.stop()
        except RuntimeError:
            logger.debug("Playwright manager stop skipped; manager was not started.")
    state.manager = None
    state.playwright = None
    state.healthy = False


def _get_launcher(state: BrowserPairState):
    if not state.playwright:
        raise RuntimeError("Playwright runtime is not initialized for this pair.")

    if state.engine == "webkit":
        return state.playwright.webkit
    if state.engine == "firefox":
        return state.playwright.firefox
    raise RuntimeError(f"Unsupported browser engine '{state.engine}'.")


async def _create_warmed_page(state: BrowserPairState, search_type: SearchType) -> Page:
    """Create a warmed Playwright page for the selected browser pair and search type."""

    context = state.award_context if search_type == "Award" else state.cash_context
    if not context:
        raise RuntimeError(f"{search_type} browser context is not initialized for {state.engine}.")

    page = await context.new_page()
    try:
        await page.goto(AA_HOMEPAGE_URL, wait_until="domcontentloaded")
        await page.wait_for_selector(AA_WARMUP_SELECTOR, timeout=AA_WARMUP_TIMEOUT)
    except TimeoutError as exc:
        await page.close()
        raise BrowserFingerprintBannedException(
            f"Failed to load AA homepage selector '{AA_WARMUP_SELECTOR}' within {AA_WARMUP_TIMEOUT}ms using {state.engine}. "
            "This likely indicates the current browser fingerprint was blocked; recycle the context, rotate fingerprints, or route through a different proxy."
        ) from exc

    return page


async def _ensure_page(state: BrowserPairState, search_type: SearchType) -> None:
    """Ensure a single warmed page exists for the specified search type."""
    page_attr = "award_page" if search_type == "Award" else "cash_page"

    # Check if page already exists and is healthy
    existing_page = getattr(state, page_attr)
    if existing_page and not existing_page.is_closed():
        return

    # Create new warmed page
    page = await _create_warmed_page(state, search_type)
    setattr(state, page_attr, page)


async def _initialize_pair(pair_key: PairKey) -> None:
    """Initialize and warm the specified browser pair."""

    state = _get_pair_state(pair_key)
    if state.healthy:
        return

    # Ensure any existing resources are closed before initializing.
    await _teardown_pair(state)

    try:
        state.manager = async_playwright()
        state.playwright = await state.manager.start()

        launcher = _get_launcher(state)
        state.award_browser = await launcher.launch(headless=True)
        state.award_context = await state.award_browser.new_context()

        state.cash_browser = await launcher.launch(headless=True)
        state.cash_context = await state.cash_browser.new_context()

        await asyncio.gather(
            _ensure_page(state, "Award"),
            _ensure_page(state, "Revenue"),
        )
        state.healthy = True
        logger.info("Initialized %s browser pair.", pair_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to initialize %s browser pair: %s", pair_key, exc)
        await _teardown_pair(state)
        raise


async def startup_browser() -> None:
    """Start both WebKit and Firefox browser pairs with warmed page pools."""

    global _active_pair, _request_counter

    async with _startup_lock:
        successes = []
        for pair_key in ("webkit", "firefox"):
            try:
                await _initialize_pair(pair_key)  # Will no-op if already healthy
            except Exception:  # noqa: BLE001
                continue
            if _get_pair_state(pair_key).healthy:
                successes.append(pair_key)

        if not successes:
            raise RuntimeError("Failed to initialize any browser pair during startup.")

        # Prefer WebKit if healthy; otherwise fall back to first successful pair.
        if _get_pair_state("webkit").healthy:
            _active_pair = "webkit"
        else:
            _active_pair = successes[0]
            logger.warning(
                "WebKit pair unavailable at startup; using %s as active pair.",
                _active_pair,
            )

        _request_counter = 0
        logger.info(
            "Browser startup complete. Active pair=%s, healthy_pairs=%s",
            _active_pair,
            successes,
        )


async def shutdown_browser() -> None:
    """Close all browser pair resources."""

    async with _startup_lock:
        for pair_key in ("webkit", "firefox"):
            state = _get_pair_state(pair_key)
            if state.manager or state.healthy:
                await _teardown_pair(state)
        logger.info("All browser pairs shut down.")


async def ensure_browser_started() -> None:
    """Ensure at least one browser pair is available and active."""

    async with _startup_lock:
        active_state = _get_pair_state(_active_pair)
        if active_state.healthy:
            return

        # Try to reinitialize the active pair first.
        try:
            await _initialize_pair(_active_pair)
        except Exception:  # noqa: BLE001
            pass

        if _get_pair_state(_active_pair).healthy:
            return

        # Fall back to the alternate pair.
        alternate = "firefox" if _active_pair == "webkit" else "webkit"
        try:
            await _initialize_pair(alternate)
        except Exception:  # noqa: BLE001
            pass

        if not _get_pair_state(alternate).healthy:
            raise RuntimeError("Unable to initialize any browser pair.")

        _switch_active_pair(alternate)


def _switch_active_pair(pair_key: PairKey) -> None:
    global _active_pair
    _active_pair = pair_key
    logger.info("Active browser pair switched to %s.", pair_key)


async def _rotate_active_pair() -> None:
    """Alternate between WebKit and Firefox pairs after threshold is met."""

    target: PairKey = "firefox" if _active_pair == "webkit" else "webkit"

    async with _startup_lock:
        state = _get_pair_state(target)
        if not state.healthy:
            try:
                await _initialize_pair(target)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Rotation skipped. Unable to activate %s pair: %s",
                    target,
                    exc,
                )
                return

        _switch_active_pair(target)


async def _record_successful_request() -> None:
    """Track successful itinerary requests and rotate pairs when threshold is reached."""

    rotate = False
    async with _request_counter_lock:
        global _request_counter
        _request_counter += 1
        if _request_counter >= _ROTATION_THRESHOLD:
            _request_counter = 0
            rotate = True

    if rotate:
        await _rotate_active_pair()


async def get_bootstrap_page(search_type: SearchType = "Award") -> Page:
    """Get the shared page for the specified search type from the active pair."""

    await ensure_browser_started()

    async with _startup_lock:
        state = _get_pair_state(_active_pair)
        page_attr = "award_page" if search_type == "Award" else "cash_page"
        page = getattr(state, page_attr)

        if not page or page.is_closed():
            page = await _create_warmed_page(state, search_type)
            setattr(state, page_attr, page)

        return page


def get_browser(search_type: SearchType = "Award") -> Browser:
    """Get the browser instance for the specified search type from the active pair."""

    state = _get_pair_state(_active_pair)
    browser = state.award_browser if search_type == "Award" else state.cash_browser
    if not browser:
        raise RuntimeError(f"{search_type} browser is not initialized for active pair {_active_pair}.")
    return browser


@asynccontextmanager
async def acquire_page(search_type: SearchType) -> AsyncIterator[Page]:
    """Acquire the shared page for the specified search type from the active pair."""

    await ensure_browser_started()

    async with _startup_lock:
        pair_key = _active_pair
        state = _get_pair_state(pair_key)
        page_attr = "award_page" if search_type == "Award" else "cash_page"
        page = getattr(state, page_attr)

        if page is None or page.is_closed():
            # Create or recreate page if doesn't exist or is closed
            page = await _create_warmed_page(state, search_type)
            setattr(state, page_attr, page)

    # Yield the shared page reference (never "returned" to pool)
    yield page


async def register_successful_request() -> None:
    """Public hook to record successful itinerary fetches."""

    await _record_successful_request()
