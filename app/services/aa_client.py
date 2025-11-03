import json
from typing import Any, Dict

from app.core.constants import API_URL
from app.services.browser_manager import (
    AA_BOOKING_URL,
    acquire_page,
    ensure_browser_started,
    register_successful_request,
)

AA_ORIGIN = "https://www.aa.com"


async def get_itinerary(
    origin: str,
    destination: str,
    date: str,
    passengers: int,
    award_search: bool,
) -> Dict[str, Any]:
    """Invoke AA's itinerary search using the shared Playwright browser."""

    await ensure_browser_started()

    payload = {
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
                "destination": destination,
                "destinationNearbyAirports": False,
                "maxStops": None,
                "origin": origin,
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

    js_code = f"""
    async (args) => {{
        const apiUrl = args.apiUrl;
        const body = args.payload;

        const headers = {{
            'accept': 'application/json, text/plain, */*',
            'content-type': 'application/json',
            'origin': '{AA_ORIGIN}',
            'referer': '{AA_BOOKING_URL}'
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

    # Route to appropriate browser based on search type
    search_type = "Award" if award_search else "Revenue"

    async with acquire_page(search_type) as page:
        result = await page.evaluate(
            js_code,
            {"apiUrl": API_URL, "payload": payload},
        )

    if not isinstance(result, dict):
        raise RuntimeError("Unexpected response payload returned by browser context.")

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

    await register_successful_request()

    return result


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
