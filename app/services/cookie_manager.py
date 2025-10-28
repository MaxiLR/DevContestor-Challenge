"""Manage shared AA session cookies with coordinated refreshes."""

from __future__ import annotations

import asyncio
from typing import Dict, Optional

from app.services import browser_manager


_cookies: Optional[Dict[str, object]] = None
_refreshing: bool = False
_condition = asyncio.Condition()


async def _refresh_cookies_internal() -> Dict[str, object]:
    cookies = await browser_manager.refresh_browser_session()
    # Playwright returns List[Cookie]; keep as-is for callers.
    return cookies


async def get_cookies() -> Dict[str, object]:
    """Return cached cookies, refreshing via Playwright when needed."""

    global _cookies, _refreshing

    async with _condition:
        if _cookies is not None and not _refreshing:
            return _cookies

        while _refreshing:
            await _condition.wait()
            if _cookies is not None and not _refreshing:
                return _cookies

        # Take responsibility for performing the refresh.
        _refreshing = True

    try:
        cookies = await _refresh_cookies_internal()
    except Exception:
        async with _condition:
            _refreshing = False
            _condition.notify_all()
        raise

    async with _condition:
        _cookies = cookies
        _refreshing = False
        _condition.notify_all()
        return _cookies


async def refresh_cookies() -> Dict[str, object]:
    """Force-refresh cookies, queuing concurrent callers until new ones are available."""

    global _cookies, _refreshing

    async with _condition:
        _cookies = None
        while _refreshing:
            await _condition.wait()
        _refreshing = True

    try:
        cookies = await _refresh_cookies_internal()
    except Exception:
        async with _condition:
            _refreshing = False
            _condition.notify_all()
        raise

    async with _condition:
        _cookies = cookies
        _refreshing = False
        _condition.notify_all()
        return _cookies
