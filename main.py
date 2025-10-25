import datetime
from playwright.sync_api import sync_playwright
import json

api_url = "https://www.aa.com/booking/api/search/itinerary"

AVAILABLE_CROSS_REFERENCES = ["MAIN", "PREMIUM_ECONOMY"]


def get_flights(origin: str, destination: str, date: str, passengers: int):
    points_itinerary = get_itinerary(
        origin, destination, date, passengers, points=True
    )["body"]["slices"]

    cash_itinerary = get_itinerary(origin, destination, date, passengers, points=False)[
        "body"
    ]["slices"]

    points_map = {flight["hash"]: flight for flight in points_itinerary}
    cash_map = {flight["hash"]: flight for flight in cash_itinerary}

    matched = [
        {"cash": cash_map[h], "points": points_map[h]}
        for h in points_map.keys() & cash_map.keys()
    ]

    return matched


def get_time(datetime_str: str):
    """Extract HH:MM from ISO-like datetime strings with optional offset."""

    normalized = datetime_str.replace("Z", "+00:00")

    try:
        parsed = datetime.datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S")

    return parsed.strftime("%H:%M")


def get_metrics(flights: dict, passengers: int, cabin_class: str):
    cabin_class = cabin_class.upper()
    if cabin_class not in AVAILABLE_CROSS_REFERENCES:
        raise ValueError(
            f"Invalid cabin class: {cabin_class}. Must be one of {AVAILABLE_CROSS_REFERENCES}"
        )

    parsed_flights = []
    for flight in flights:
        points_dict = flight["points"]
        cash_dict = flight["cash"]

        points_price_obj = [
            x
            for x in points_dict["productPricing"]
            if f'"{cabin_class}"' in json.dumps(x)
        ][0]["regularPrice"]
        cash_price_obj = cash_dict["productGroups"][cabin_class][0]

        try:
            points_required = int(
                points_price_obj["slicePricing"]["perPassengerAwardPoints"] * passengers
            )
            taxes_fees_usd = float(
                points_price_obj["slicePricing"]["allPassengerDisplayTotal"]["amount"]
            )
            cash_price_usd = float(
                cash_price_obj["slicePricing"]["allPassengerDisplayTotal"]["amount"]
            )
        except:
            continue

        parsed_flights.append(
            {
                "flight_number": "AA123",
                "departure_time": get_time(cash_dict["departureDateTime"]),
                "arrival_time": get_time(cash_dict["arrivalDateTime"]),
                "points_required": points_required,
                "cash_price_usd": cash_price_usd,
                "taxes_fees_usd": taxes_fees_usd,
                "cpp": calculate_cpp(
                    cash_price=cash_price_usd,
                    taxes=taxes_fees_usd,
                    points=points_required,
                ),
            }
        )

    return parsed_flights


def get_itinerary(
    origin: str,
    destination: str,
    date: str,
    passengers: int,
    points: bool = True,
):
    """Get the AA itinerary with dynamic search parameters."""

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
                "searchType": "Award" if points else "Revenue",
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
        browser.close()

    result["body"] = json.loads(result["body"])

    return result


def calculate_cpp(cash_price: float, taxes: float, points: int):
    return ((cash_price - taxes) / points) * 100


def main(origin: str, destination: str, date: str, passengers: int, cabin_class: str):
    flights = get_flights(
        origin=origin,
        destination=destination,
        date=date,
        passengers=passengers,
    )
    parsed_flights = get_metrics(
        flights=flights, passengers=passengers, cabin_class=cabin_class
    )

    return {
        "search_metadata": {
            "origin": origin,
            "destination": destination,
            "date": date,
            "passengers": passengers,
            "cabin_class": cabin_class,
        },
        "flights": parsed_flights,
        "total_results": len(parsed_flights),
    }


if __name__ == "__main__":
    print(
        main(
            origin="LAX",
            destination="JFK",
            date="2025-12-15",
            passengers=1,
            cabin_class="premium_economy",
        )
    )
