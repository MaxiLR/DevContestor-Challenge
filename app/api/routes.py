from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import FlightsResponse, HealthResponse
from app.services.flight_service import build_search_results

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/flights", response_model=FlightsResponse)
async def flights_endpoint(
    origin: str = Query(..., min_length=3, max_length=3, description="Origin airport IATA code."),
    destination: str = Query(..., min_length=3, max_length=3, description="Destination airport IATA code."),
    date: str = Query(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Departure date in YYYY-MM-DD format.",
    ),
    passengers: int = Query(1, ge=1, le=9, description="Number of adult passengers."),
    cabin_class: str = Query(
        "economy",
        description="Cabin cross-reference. Supported values: main, premium_economy.",
    ),
) -> FlightsResponse:
    try:
        payload = await build_search_results(
            origin=origin,
            destination=destination,
            date=date,
            passengers=passengers,
            cabin_class=cabin_class,
        )
        return FlightsResponse.model_validate(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
