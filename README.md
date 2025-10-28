[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue?logo=python)](https://www.python.org/) [![FastAPI](https://img.shields.io/badge/FastAPI-async-green?logo=fastapi)](https://fastapi.tiangolo.com/) [![Playwright](https://img.shields.io/badge/Playwright-firefox-lightgrey?logo=playwright)](https://playwright.dev/) [![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker)](https://www.docker.com/)

# Operation Point Break

American Airlines hides the side-by-side comparison that would show whether cash or award bookings are the better deal. This project automates those checks and exposes the results through a simple FastAPI service.

## Assumptions
- The challenge provides a single departure date, so searches are performed as **One-Way** itineraries.
- Only the `MAIN` and `PREMIUM_ECONOMY` cross-reference buckets can be reliably compared across cash and award responses today.
- Award and cash data are matched via the `hash` identifier returned by AA's internal API; if either side omits a hash the flight is ignored.
- The scraper currently targets adult passengers and expects airport codes in IATA format.

## Technologies Used
- Python 3.12
- Playwright (headless Firefox)
- FastAPI & Uvicorn
- uv (dependency and runtime manager)
- Docker (deployment packaging)

## Project Structure
```
.
├── app/
│   ├── api/              # FastAPI routers
│   ├── core/             # Shared constants and configuration
│   ├── models/           # Pydantic response schemas
│   └── services/         # Scraper + aggregation logic
├── main.py               # CLI entrypoint delegating to app.main
├── Dockerfile            # Container image definition
├── pyproject.toml        # Project metadata
└── uv.lock               # Locked dependency graph
```

## Approach and Implementation
1. A Playwright Firefox context warms AA's booking page to harvest real session cookies, user-agent, and locale hints. A shared cookie manager guards refreshes so only one request hits the browser when the session expires.
2. Each itinerary search (award + cash) is executed with `httpx` over HTTP/2 using the warmed browser cookies for speed.
3. When AA rejects the synthetic client (auth/rate issues), the same request is retried through Playwright’s in-browser `fetch`, which also refreshes the cookie jar for subsequent calls.
4. Flights are intersected via the shared `hash` field. Each surviving flight pulls departure/arrival timestamps, the relevant cabin product groups, and the per-passenger prices.
5. CPP is computed as `(cash_price_usd - taxes_fees_usd) / points_required × 100`, rounded to two decimals. Responses that lack the needed pricing blocks are skipped.
6. FastAPI exposes `/flights` for the comparison and `/health` for readiness checks. Errors from AA's API propagate as `502` responses, while validation issues (e.g., unsupported cabin class) surface as `400`s.

## Running Locally
1. Install dependencies and browsers:
   ```bash
   uv sync
   uv run playwright install firefox
   ```
2. Start the API server (this bootstraps the browser and hydrates cookies):
   ```bash
   uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
3. Call the comparison endpoint:
   ```bash
   curl "http://localhost:8000/flights?origin=LAX&destination=JFK&date=2025-12-15&passengers=1&cabin_class=main"
   ```

## Running with Docker
```bash
docker build -t point-break .
docker run --rm -p 8000:8000 point-break
```

## API Endpoints
- `GET /health` → `{ "status": "ok" }`
- `GET /flights` → accepts `origin`, `destination`, `date`, `passengers`, `cabin_class` as query string parameters and returns the pricing comparison payload.

## Example output.json
```json
{
  "search_metadata": {
    "origin": "LAX",
    "destination": "JFK",
    "date": "2025-12-15",
    "passengers": 1,
    "cabin_class": "economy"
  },
  "flights": [
    {
      "flight_number": "AA123",
      "departure_time": "08:00",
      "arrival_time": "16:30",
      "points_required": 12500,
      "cash_price_usd": 289.0,
      "taxes_fees_usd": 5.6,
      "cpp": 2.27
    },
    {
      "flight_number": "AA456",
      "departure_time": "14:15",
      "arrival_time": "22:45",
      "points_required": 10000,
      "cash_price_usd": 189.0,
      "taxes_fees_usd": 5.6,
      "cpp": 1.83
    }
  ],
  "total_results": 2
}
```

> The example illustrates the required response shape; live numbers depend on real-time AA availability.

## Additional Notes
- The fast path uses `httpx`; Playwright only takes over after repeated authentication failures. Tweak the headers captured in `browser_manager` if AA tightens detection.
- Extend the `AVAILABLE_CROSS_REFERENCES` list to support more cabins once AA exposes consistent identifiers for them across award and cash searches.
