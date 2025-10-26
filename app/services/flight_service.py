import datetime
import json
from typing import Any, Dict, List

from app.core.constants import AVAILABLE_CROSS_REFERENCES
from app.services.aa_client import fetch_itinerary


def get_time(datetime_str: str) -> str:
    """Extract HH:MM from ISO-like datetime strings with optional offset."""

    if not datetime_str:
        raise ValueError("Datetime string is required to extract time.")

    normalized = datetime_str.replace("Z", "+00:00")

    try:
        parsed = datetime.datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S")

    return parsed.strftime("%H:%M")


def calculate_cpp(cash_price: float, taxes: float, points: int) -> float:
    if points <= 0:
        raise ValueError("Points value must be greater than zero to compute CPP.")

    return ((cash_price - taxes) / points) * 100


def _match_flights(
    origin: str,
    destination: str,
    date: str,
    passengers: int,
) -> List[Dict[str, Any]]:
    points_response = fetch_itinerary(
        origin=origin,
        destination=destination,
        date=date,
        passengers=passengers,
        award_search=True,
    )
    cash_response = fetch_itinerary(
        origin=origin,
        destination=destination,
        date=date,
        passengers=passengers,
        award_search=False,
    )

    points_slices = points_response["body"].get("slices", [])
    cash_slices = cash_response["body"].get("slices", [])

    points_map = {
        flight.get("hash"): flight for flight in points_slices if flight.get("hash")
    }
    cash_map = {
        flight.get("hash"): flight for flight in cash_slices if flight.get("hash")
    }

    return [
        {"cash": cash_map[hash_id], "points": points_map[hash_id]}
        for hash_id in points_map.keys() & cash_map.keys()
    ]


def _parse_flights(
    flights: List[Dict[str, Any]], passengers: int, cabin_class: str
) -> List[Dict[str, Any]]:
    normalized_cabin = cabin_class.upper()
    if normalized_cabin not in AVAILABLE_CROSS_REFERENCES:
        raise ValueError(
            f"Invalid cabin class: {normalized_cabin}. Must be one of {AVAILABLE_CROSS_REFERENCES}"
        )

    parsed_flights: List[Dict[str, Any]] = []
    for flight in flights:
        points_dict = flight.get("points") or {}
        cash_dict = flight.get("cash") or {}

        points_price_obj = next(
            (
                entry.get("regularPrice")
                for entry in points_dict.get("productPricing", [])
                if f'"{normalized_cabin}"' in json.dumps(entry)
            ),
            None,
        )
        cash_price_candidates = (cash_dict.get("productGroups") or {}).get(
            normalized_cabin
        ) or []

        if not points_price_obj or not cash_price_candidates:
            continue

        cash_price_obj = cash_price_candidates[0]

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
            departure_time = get_time(cash_dict.get("departureDateTime"))
            arrival_time = get_time(cash_dict.get("arrivalDateTime"))
            cpp_value = round(
                calculate_cpp(
                    cash_price=cash_price_usd,
                    taxes=taxes_fees_usd,
                    points=points_required,
                ),
                2,
            )
        except (KeyError, TypeError, ValueError):
            continue

        parsed_flights.append(
            {
                "flight_number": f"AA{points_dict['segments'][0]['flight']['flightNumber']}",
                "departure_time": departure_time,
                "arrival_time": arrival_time,
                "points_required": points_required,
                "cash_price_usd": round(cash_price_usd, 2),
                "taxes_fees_usd": round(taxes_fees_usd, 2),
                "cpp": cpp_value,
            }
        )

    return parsed_flights


def build_search_results(
    origin: str,
    destination: str,
    date: str,
    passengers: int,
    cabin_class: str,
) -> Dict[str, Any]:
    if passengers <= 0:
        raise ValueError("Number of passengers must be at least 1.")

    origin_code = origin.upper()
    destination_code = destination.upper()
    cabin_normalized = cabin_class.lower()

    flights = _match_flights(
        origin=origin_code,
        destination=destination_code,
        date=date,
        passengers=passengers,
    )

    parsed_flights = _parse_flights(
        flights=flights,
        passengers=passengers,
        cabin_class=cabin_normalized,
    )

    return {
        "search_metadata": {
            "origin": origin_code,
            "destination": destination_code,
            "date": date,
            "passengers": passengers,
            "cabin_class": cabin_normalized,
        },
        "flights": parsed_flights,
        "total_results": len(parsed_flights),
    }
