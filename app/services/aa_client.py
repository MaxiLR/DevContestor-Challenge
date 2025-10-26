import json
from typing import Any, Dict

from playwright.sync_api import sync_playwright

from app.core.constants import API_URL


def fetch_itinerary(
    origin: str,
    destination: str,
    date: str,
    passengers: int,
    award_search: bool,
) -> Dict[str, Any]:
    """Invoke AA's itinerary search from within a Playwright browser context."""

    payload = json.dumps(
        {
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
    )

    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.aa.com/booking/choose-flights/1")
        page.wait_for_load_state("domcontentloaded")

        js_code = f"""
        async () => {{
            const apiUrl = "{API_URL}";
            const body = {payload};

            const headers = {{
                'accept': 'application/json, text/plain, */*',
                'content-type': 'application/json',
                'origin': location.origin,
                'referer': location.href
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
            }} catch (e) {{
                return {{ error: String(e) }};
            }}
        }}
        """

        result = page.evaluate(js_code)
        browser.close()

    if not isinstance(result, dict):
        raise RuntimeError("Unexpected response payload returned by browser context.")

    if "error" in result:
        raise RuntimeError(result["error"])

    status = result.get("status")
    if isinstance(status, int) and status >= 400:
        raise RuntimeError(
            f"AA API responded with HTTP {status}: {result.get('statusText')}"
        )

    body_text = result.get("body")
    if not body_text:
        raise RuntimeError("AA API returned an empty body.")

    try:
        result["body"] = json.loads(body_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Unable to parse AA API response body.") from exc

    return result
