from urllib.parse import urlsplit

import pytest

from app.api import health
from app.api.v1.url_shortner import router as url_router
from app.services.rate_limiter import RateLimitDecision


pytestmark = pytest.mark.anyio


async def test_create_and_resolve_short_url(client):
    response = await client.post(
        "/shorten",
        json={"url": "https://example.com/a/long/path"},
    )

    assert response.status_code == 200
    assert "x-ratelimit-limit" not in response.headers
    assert "x-ratelimit-remaining" not in response.headers

    short_url = response.json()["short_url"]
    path = urlsplit(short_url).path
    assert short_url.startswith("http://testserver/")
    assert len(path.removeprefix("/")) == 10

    redirect = await client.get(path, follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "https://example.com/a/long/path"

    metrics = await client.get("/metrics")
    assert 'route="/{code}"' in metrics.text


async def test_invalid_and_unknown_urls(client):
    invalid = await client.post("/shorten", json={"url": "not-a-url"})
    missing = await client.get("/abcdefghij", follow_redirects=False)
    malformed_code = await client.get("/not-valid!", follow_redirects=False)

    assert invalid.status_code == 422
    assert missing.status_code == 404
    assert malformed_code.status_code == 422


async def test_operational_routes_are_not_captured_as_codes(
    client,
    monkeypatch,
):
    monkeypatch.setattr(health, "redis_capabilities_ready", lambda *_: True)

    assert (await client.get("/")).status_code == 200
    assert (await client.get("/health/live")).json() == {"status": "ok"}
    assert (await client.get("/health/ready")).status_code == 200

    metrics = await client.get("/metrics")
    assert metrics.status_code == 200
    assert "snaplink_http_requests_total" in metrics.text

    favicon = await client.get("/favicon.ico")
    assert favicon.status_code == 204


async def test_readiness_degrades_when_optional_redis_is_down(
    client,
    monkeypatch,
):
    monkeypatch.setattr(health, "redis_capabilities_ready", lambda *_: False)

    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["database"] == "ok"
    assert response.json()["checks"]["redis"] == "unavailable"


async def test_readiness_handles_redis_client_configuration_failure(
    client,
    monkeypatch,
):
    def invalid_client():
        raise ValueError("invalid URL")

    monkeypatch.setattr(health, "get_cache_redis_client", invalid_client)

    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


async def test_rate_limit_rejection_has_retry_headers(client, monkeypatch):
    monkeypatch.setattr(
        url_router,
        "check_rate_limit",
        lambda *_: RateLimitDecision(
            allowed=False,
            remaining=0,
            retry_after=42,
        ),
    )

    response = await client.post(
        "/shorten",
        json={"url": "https://example.com"},
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "42"
    assert response.headers["x-ratelimit-remaining"] == "0"


async def test_rate_limit_backend_failure_is_not_reported_as_quota(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        url_router,
        "check_rate_limit",
        lambda *_: RateLimitDecision(
            allowed=False,
            remaining=0,
            retry_after=10,
            available=False,
        ),
    )

    response = await client.post(
        "/shorten",
        json={"url": "https://example.com"},
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "10"


async def test_fail_open_does_not_advertise_unknown_quota(client, monkeypatch):
    monkeypatch.setattr(
        url_router,
        "check_rate_limit",
        lambda *_: RateLimitDecision(
            allowed=True,
            remaining=100,
            retry_after=0,
            available=False,
        ),
    )

    response = await client.post(
        "/shorten",
        json={"url": "https://example.com"},
    )

    assert response.status_code == 200
    assert "x-ratelimit-limit" not in response.headers
    assert "x-ratelimit-remaining" not in response.headers
