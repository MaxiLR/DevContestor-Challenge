import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.core.constants import API_URL
from app.services import aa_client


def _response(status: int, body: dict | None = None, text: str | None = None) -> httpx.Response:
    request = httpx.Request("POST", API_URL)
    if body is not None:
        return httpx.Response(status, request=request, json=body)
    return httpx.Response(status, request=request, text=text or "")


class GetItineraryTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefers_httpx_with_valid_response(self) -> None:
        response_body = {
            "responseMetadata": {"sessionId": "abc", "solutionSet": "123", "sliceCount": 1},
            "products": ["sample"],
        }
        httpx_response = _response(200, body=response_body)

        with (
            patch("app.services.aa_client.get_cookies", new=AsyncMock(return_value={"cookies": []})) as mock_get,
            patch("app.services.aa_client._perform_request", new=AsyncMock(return_value=httpx_response)) as mock_httpx,
            patch("app.services.aa_client._perform_playwright_fetch", new=AsyncMock()) as mock_fallback,
            patch("app.services.aa_client.refresh_cookies", new=AsyncMock()) as mock_refresh,
        ):
            result = await aa_client.get_itinerary(
                origin="lax",
                destination="jfk",
                date="2025-12-15",
                passengers=1,
                award_search=True,
            )

        self.assertEqual(result["status"], 200)
        self.assertEqual(result["body"], response_body)

        mock_get.assert_awaited_once()
        mock_httpx.assert_awaited_once()
        mock_fallback.assert_not_called()
        mock_refresh.assert_not_awaited()

        payload_arg = mock_httpx.await_args.args[0]
        self.assertEqual(payload_arg["slices"][0]["origin"], "LAX")
        self.assertEqual(payload_arg["slices"][0]["destination"], "JFK")
        self.assertEqual(payload_arg["tripOptions"]["searchType"], "Award")

    async def test_fallback_used_after_refresh_failures(self) -> None:
        httpx_fail = _response(401, text="auth failure")
        fallback_payload = {
            "status": 200,
            "statusText": "OK",
            "url": "https://example.com",
            "headers": {"content-type": "application/json"},
            "body": {"responseMetadata": {}},
            "summary": {"sliceCount": 0},
        }

        with (
            patch("app.services.aa_client.get_cookies", new=AsyncMock(return_value={"cookies": []})) as mock_get,
            patch(
                "app.services.aa_client._perform_request",
                new=AsyncMock(side_effect=[httpx_fail, httpx_fail]),
            ) as mock_httpx,
            patch(
                "app.services.aa_client._perform_playwright_fetch",
                new=AsyncMock(return_value=fallback_payload),
            ) as mock_fallback,
            patch(
                "app.services.aa_client.refresh_cookies",
                new=AsyncMock(side_effect=[{"cookies": []}, {"cookies": []}, {"cookies": []}]),
            ) as mock_refresh,
        ):
            result = await aa_client.get_itinerary(
                origin="dfw",
                destination="mia",
                date="2025-11-01",
                passengers=2,
                award_search=False,
            )

        self.assertEqual(result, fallback_payload)

        mock_get.assert_awaited_once()
        self.assertEqual(mock_httpx.await_count, 2)
        mock_fallback.assert_awaited_once()
        # One refresh for each HTTP 401 plus one after fallback success.
        self.assertEqual(mock_refresh.await_count, 3)

    async def test_fallback_error_propagates(self) -> None:
        httpx_fail = _response(401, text="auth failure")

        with (
            patch("app.services.aa_client.get_cookies", new=AsyncMock(return_value={"cookies": []})),
            patch(
                "app.services.aa_client._perform_request",
                new=AsyncMock(side_effect=[httpx_fail, httpx_fail]),
            ),
            patch(
                "app.services.aa_client._perform_playwright_fetch",
                new=AsyncMock(side_effect=RuntimeError("fallback broke")),
            ),
            patch(
                "app.services.aa_client.refresh_cookies",
                new=AsyncMock(side_effect=[{"cookies": []}, {"cookies": []}]),
            ) as mock_refresh,
        ):
            with self.assertRaisesRegex(RuntimeError, "fallback broke"):
                await aa_client.get_itinerary(
                    origin="dfw",
                    destination="mia",
                    date="2025-11-01",
                    passengers=1,
                    award_search=False,
                )

        # refresh called twice for refresh status codes; fallback failure prevents final refresh
        self.assertEqual(mock_refresh.await_count, 2)


class ShutdownHttpClientTests(unittest.TestCase):
    def test_shutdown_http_client_idempotent(self) -> None:
        client = httpx.AsyncClient()
        aa_client._client = client  # type: ignore[attr-defined]

        asyncio.run(aa_client.shutdown_http_client())
        self.assertIsNone(aa_client._client)

        # Second call should be a no-op
        asyncio.run(aa_client.shutdown_http_client())
