from typing import List

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(default="ok", description="Service status indicator")


class SearchMetadata(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    passengers: int = Field(..., ge=1)
    cabin_class: str


class Flight(BaseModel):
    flight_number: str
    departure_time: str
    arrival_time: str
    points_required: int
    cash_price_usd: float
    taxes_fees_usd: float
    cpp: float


class FlightsResponse(BaseModel):
    search_metadata: SearchMetadata
    flights: List[Flight]
    total_results: int
