import json
from typing import Any, Dict

from playwright._impl._errors import TargetClosedError
from playwright.async_api import TimeoutError

from app.core.constants import API_URL
from app.services.browser_manager import (
    AA_BOOKING_URL,
    acquire_page,
    ensure_browser_started,
    refresh_browser_session,
)

AA_ORIGIN = "https://www.aa.com"
AA_REFERER = AA_BOOKING_URL
_REFRESH_STATUS_CODES = {401, 403, 419, 429, 460, 503}
_MAX_ATTEMPTS = 3
_NAV_TIMEOUT_MS = 45_000

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
        'sec-fetch-dest': 'empty',
        'cache-control': 'no-cache',
        'pragma': 'no-cache'
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


def _parse_successful_payload(raw_result: Dict[str, Any]) -> Dict[str, Any]:
    body_text = raw_result.get("body")
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
        "status": raw_result.get("status"),
        "statusText": raw_result.get("statusText"),
        "url": raw_result.get("url"),
        "headers": raw_result.get("headers"),
        "body": parsed_body,
        "summary": summary,
    }


async def _perform_fetch(payload: Dict[str, Any]) -> Dict[str, Any]:
    await ensure_browser_started()

    last_error: Exception | None = None

    for attempt in range(_MAX_ATTEMPTS):
        try:
            async with acquire_page() as page:
                await page.goto(
                    AA_REFERER,
                    wait_until="domcontentloaded",
                    timeout=_NAV_TIMEOUT_MS,
                )
                await page.wait_for_selector("h1", timeout=_NAV_TIMEOUT_MS)
                result = await page.evaluate(
                    _PLAYWRIGHT_FETCH_SNIPPET,
                    {"apiUrl": API_URL, "payload": payload},
                )
        except TargetClosedError as exc:
            last_error = exc
            await refresh_browser_session()
            continue
        except TimeoutError as exc:
            last_error = exc
            await refresh_browser_session()
            continue
        except Exception as exc:  # pragma: no cover - unexpected
            last_error = exc
            break

        if not isinstance(result, dict):
            raise RuntimeError("Unexpected response payload returned by browser context.")

        if "error" in result:
            raise RuntimeError(result["error"])

        status = result.get("status")
        if not isinstance(status, int):
            raise RuntimeError("AA API response missing status code.")

        if status in _REFRESH_STATUS_CODES:
            last_error = RuntimeError(
                f"AA API responded with HTTP {status}: {result.get('body', '')}"
            )
            await refresh_browser_session()
            continue

        if status >= 400:
            raise RuntimeError(f"AA API responded with HTTP {status}: {result.get('body', '')}")

        return _parse_successful_payload(result)

    if last_error is not None:
        raise RuntimeError("Unable to retrieve itinerary after multiple attempts.") from last_error

    raise RuntimeError("Unable to retrieve itinerary after multiple attempts.")


async def get_itinerary(
    origin: str,
    destination: str,
    date: str,
    passengers: int,
    award_search: bool,
) -> Dict[str, Any]:
    """Invoke AA's itinerary search using Playwright, retrying on anti-bot status codes."""

    payload = _build_payload(
        origin=origin,
        destination=destination,
        date=date,
        passengers=passengers,
        award_search=award_search,
    )

    return await _perform_fetch(payload)


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
