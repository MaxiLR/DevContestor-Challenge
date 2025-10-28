import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import httpx
from playwright._impl._errors import TargetClosedError

from app.core.constants import API_URL
from app.services import browser_manager
from app.services.cookie_manager import get_cookies, refresh_cookies

AA_ORIGIN = "https://www.aa.com"
AA_REFERER = "https://www.aa.com/booking/choose-flights/1"
_REFRESH_STATUS_CODES = {401, 403, 419, 429}

_client_lock = asyncio.Lock()
_client: Optional[httpx.AsyncClient] = None

logging.getLogger("httpx").setLevel(logging.WARNING)


def _build_cookie_jar(cookies: List[Dict[str, Any]]) -> httpx.Cookies:
    jar = httpx.Cookies()
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue

        domain = cookie.get("domain")
        path = cookie.get("path") or "/"
        jar.set(name, str(value), domain=domain, path=path)

    return jar


async def _get_http_client() -> httpx.AsyncClient:
    global _client

    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = httpx.AsyncClient(http2=True, timeout=httpx.Timeout(30.0))

    return _client


async def shutdown_http_client() -> None:
    global _client

    async with _client_lock:
        if _client is not None:
            await _client.aclose()
            _client = None


_PLAYWRIGHT_FETCH_SNIPPET = f"""
async (args) => {{
    const apiUrl = args.apiUrl;
    const body = args.payload;

    const headers = {{
        'accept': 'application/json, text/plain, */*',
        'content-type': 'application/json',
        'origin': '{AA_ORIGIN}',
        'referer': '{AA_REFERER}',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-mode': 'cors',
        'sec-fetch-dest': 'empty'
    }};

    try {{
        const res = await fetch(apiUrl, {{
            method: 'POST',
            credentials: 'include',
            headers,
            body: JSON.stringify(body),
        }});
        const text = await res.text();

        let summary = null;
        try {{
            const parsed = JSON.parse(text);
            summary = {{
                sessionId: parsed?.responseMetadata?.sessionId || null,
                solutionSet: parsed?.responseMetadata?.solutionSet || null,
                sliceCount: parsed?.responseMetadata?.sliceCount || null,
                products: parsed?.products || null,
            }};
        }} catch {{}}

        return {{
            status: res.status,
            statusText: res.statusText,
            url: res.url,
            headers: Object.fromEntries(res.headers.entries()),
            body: text,
            summary
        }};
    }} catch (error) {{
        return {{ error: String(error) }};
    }}
}}
"""


async def _perform_request(
    payload: Dict[str, Any],
    cookies_bundle: Dict[str, Any],
) -> httpx.Response:
    client = await _get_http_client()

    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": AA_ORIGIN,
        "referer": AA_REFERER,
    }

    user_agent = cookies_bundle.get("user_agent")
    if isinstance(user_agent, str):
        headers["user-agent"] = user_agent

    accept_language = cookies_bundle.get("language")
    languages = cookies_bundle.get("languages")
    if isinstance(accept_language, str):
        headers["accept-language"] = accept_language
    if isinstance(languages, list) and languages:
        headers.setdefault("accept-language", ",".join(languages))

    headers.setdefault("sec-fetch-site", "same-origin")
    headers.setdefault("sec-fetch-mode", "cors")
    headers.setdefault("sec-fetch-dest", "empty")

    cookies = cookies_bundle.get("cookies") or []
    jar = _build_cookie_jar(cookies)

    response = await client.post(
        API_URL,
        json=payload,
        headers=headers,
        cookies=jar,
    )
    return response


async def _perform_playwright_fetch(payload: Dict[str, Any]) -> Dict[str, Any]:
    await browser_manager.ensure_browser_started()

    last_exception: Optional[Exception] = None

    for _ in range(3):
        context = browser_manager.get_browser_context()
        page = None

        try:
            page = await context.new_page()
            await page.goto(AA_REFERER)
            await page.wait_for_selector("h1")
            result = await page.evaluate(
                _PLAYWRIGHT_FETCH_SNIPPET,
                {"apiUrl": API_URL, "payload": payload},
            )
        except TargetClosedError as exc:
            last_exception = exc
            await refresh_cookies()
            continue
        finally:
            if page:
                try:
                    await page.close()
                except TargetClosedError:
                    pass

        if not isinstance(result, dict):
            raise RuntimeError(
                "Unexpected response payload returned by browser context."
            )

        if "error" in result:
            raise RuntimeError(result["error"])

        status = result.get("status")
        if isinstance(status, int) and status >= 400:
            raise RuntimeError(
                f'AA API responded with HTTP {status}: {result.get("body", "")}'
            )

        body_text = result.get("body")
        if not body_text:
            raise RuntimeError("AA API returned an empty body.")

        try:
            parsed_body = json.loads(body_text)
            result["body"] = parsed_body
        except json.JSONDecodeError as exc:
            raise RuntimeError("Unable to parse AA API response body.") from exc

        return result

    raise RuntimeError(
        "Unable to execute fallback fetch after multiple attempts"
    ) from last_exception


def _build_payload(
    origin: str,
    destination: str,
    date: str,
    passengers: int,
    award_search: bool,
) -> Dict[str, Any]:
    return {
        "metadata": {
            "selectedProducts": [],
            "tripType": "OneWay",
            "udo": {"search_method": "Lowest"},
        },
        "passengers": [{"type": "adult", "count": passengers}],
        "requestHeader": {"clientId": "AAcom"},
        "slices": [
            {
                "allCarriers": True,
                "cabin": "",
                "departureDate": date,
                "destination": destination.upper(),
                "destinationNearbyAirports": False,
                "maxStops": None,
                "origin": origin.upper(),
                "originNearbyAirports": False,
            }
        ],
        "tripOptions": {
            "corporateBooking": False,
            "fareType": "Lowest",
            "locale": "en_US",
            "pointOfSale": None,
            "searchType": "Award" if award_search else "Revenue",
        },
        "loyaltyInfo": None,
        "version": "cfr",
        "queryParams": {
            "sliceIndex": 0,
            "sessionId": "",
            "solutionSet": "",
            "solutionId": "",
            "sort": "CARRIER",
        },
    }


async def get_itinerary(
    origin: str,
    destination: str,
    date: str,
    passengers: int,
    award_search: bool,
) -> Dict[str, Any]:
    """Invoke AA's itinerary search using httpx, falling back to Playwright when needed."""

    payload = _build_payload(
        origin=origin,
        destination=destination,
        date=date,
        passengers=passengers,
        award_search=award_search,
    )

    cookies_bundle = await get_cookies()

    for _ in range(2):
        response = await _perform_request(payload, cookies_bundle)

        if response.status_code in _REFRESH_STATUS_CODES:
            cookies_bundle = await refresh_cookies()
            continue

        if response.status_code >= 400:
            raise RuntimeError(
                f"AA API responded with HTTP {response.status_code}: {response.text}"
            )

        body_text = response.text
        if not body_text:
            raise RuntimeError("AA API returned an empty body.")

        try:
            parsed_body = json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Unable to parse AA API response body.") from exc

        summary = {
            "sessionId": parsed_body.get("responseMetadata", {}).get("sessionId"),
            "solutionSet": parsed_body.get("responseMetadata", {}).get("solutionSet"),
            "sliceCount": parsed_body.get("responseMetadata", {}).get("sliceCount"),
            "products": parsed_body.get("products"),
        }

        return {
            "status": response.status_code,
            "statusText": response.reason_phrase,
            "url": str(response.url),
            "headers": dict(response.headers.items()),
            "body": parsed_body,
            "summary": summary,
        }

    fallback_result = await _perform_playwright_fetch(payload)

    # After a successful browser fetch, refresh stored cookies
    await refresh_cookies()

    return fallback_result


async def fetch_itinerary(
    origin: str,
    destination: str,
    date: str,
    passengers: int,
    award_search: bool,
) -> Dict[str, Any]:
    """Maintained for backwards compatibility; delegates to get_itinerary."""

    return await get_itinerary(
        origin=origin,
        destination=destination,
        date=date,
        passengers=passengers,
        award_search=award_search,
    )
