import pytest

from app.api import auth
from app.config import get_settings
from app.services.rate_limiter import RateLimitDecision


@pytest.mark.anyio
async def test_account_owns_only_its_links(client, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    get_settings.cache_clear()

    first = await client.post(
        "/auth/register",
        json={"email": "first@example.com", "password": "correct-horse-battery"},
    )
    assert first.status_code == 201
    assert first.json()["plan"] == "free"

    created = await client.post("/shorten", json={"url": "https://example.com/private"})
    assert created.status_code == 200
    first_short_url = created.json()["short_url"]

    dashboard = await client.get("/account/links")
    assert dashboard.status_code == 200
    assert [entry["short_url"] for entry in dashboard.json()] == [first_short_url]

    overview = await client.get("/account/overview")
    assert overview.status_code == 200
    assert overview.json() == {
        "plan": "free",
        "saved_link_count": 1,
        "features": ["Private link dashboard"],
    }

    assert (await client.post("/auth/logout")).status_code == 204
    second = await client.post(
        "/auth/register",
        json={"email": "second@example.com", "password": "another-correct-password"},
    )
    assert second.status_code == 201
    assert (await client.get("/account/links")).json() == []

    # Sharing a short URL still works; the dashboard ownership is private.
    code = first_short_url.rsplit("/", 1)[1]
    redirect = await client.get(f"/{code}", follow_redirects=False)
    assert redirect.status_code == 307


@pytest.mark.anyio
async def test_shortening_requires_login_when_accounts_enabled(client, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    get_settings.cache_clear()

    response = await client.post("/shorten", json={"url": "https://example.com"})
    assert response.status_code == 401


@pytest.mark.anyio
async def test_anyone_can_shorten_when_accounts_are_optional(client, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    get_settings.cache_clear()

    response = await client.post("/shorten", json={"url": "https://example.com"})
    assert response.status_code == 200


@pytest.mark.anyio
async def test_account_entry_serves_the_account_page(client):
    for path in ("/register", "/login"):
        response = await client.get(path)
        assert response.status_code == 200
        assert "auth-form" in response.text


@pytest.mark.anyio
async def test_auth_attempts_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(
        auth,
        "check_rate_limit",
        lambda *_args, **_kwargs: RateLimitDecision(
            allowed=False,
            remaining=0,
            retry_after=60,
        ),
    )

    response = await client.post(
        "/auth/login",
        json={"email": "person@example.com", "password": "correct-horse-battery"},
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
