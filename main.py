from playwright.sync_api import sync_playwright
import json

api_url = "https://www.aa.com/booking/api/search/itinerary"

payload = json.dumps(
    {
        "metadata": {
            "selectedProducts": [],
            "tripType": "RoundTrip",
            "udo": {"search_method": "Lowest"},
        },
        "passengers": [{"type": "adult", "count": 1}],
        "requestHeader": {"clientId": "AAcom"},
        "slices": [
            {
                "allCarriers": True,
                "cabin": "",
                "departureDate": "2025-11-11",
                "destination": "JFK",
                "destinationNearbyAirports": False,
                "maxStops": None,
                "origin": "COR",
                "originNearbyAirports": False,
            },
            {
                "allCarriers": True,
                "cabin": "",
                "departureDate": "2025-12-11",
                "destination": "COR",
                "destinationNearbyAirports": False,
                "maxStops": None,
                "origin": "JFK",
                "originNearbyAirports": False,
            },
        ],
        "tripOptions": {
            "corporateBooking": False,
            "fareType": "Lowest",
            "locale": "en_US",
            "pointOfSale": None,
            "searchType": "Revenue",
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

with sync_playwright() as p:
    browser = p.firefox.launch(headless=True)
    page = browser.new_page()
    page.goto("https://www.aa.com/booking/choose-flights/1")
    page.wait_for_load_state("domcontentloaded")

    # PoC: Perform the itinerary POST from within the page using minimal headers
    js_code = f"""
    async () => {{
        const apiUrl = "{api_url}";
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
    print("Response Status:", result.get("status"))
    print("Response Body:")
    print(result.get("body"))

    if result.get("summary"):
        print("Summary:", json.dumps(result["summary"], indent=2))
    else:
        print("No JSON summary available")

    browser.close()
