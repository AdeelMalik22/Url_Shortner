import pytest

from app.config import get_settings


@pytest.mark.anyio
async def test_account_owns_only_its_links(client, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    get_settings.cache_clear()

    first = await client.post(
        "/auth/register",
        json={"email": "first@example.com", "password": "correct-horse-battery"},
    )
    assert first.status_code == 201

    created = await client.post("/shorten", json={"url": "https://example.com/private"})
    assert created.status_code == 200
    first_short_url = created.json()["short_url"]

    dashboard = await client.get("/account/links")
    assert dashboard.status_code == 200
    assert [entry["short_url"] for entry in dashboard.json()] == [first_short_url]

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
    response = await client.get("/register")
    assert response.status_code == 200
    assert "auth-form" in response.text
