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
        json={
            "first_name": "First",
            "last_name": "Person",
            "username": "first_person",
            "email": "first@example.com",
            "password": "correct-horse-battery",
            "confirm_password": "correct-horse-battery",
        },
    )
    assert first.status_code == 201
    assert first.json()["plan"] == "free"
    assert first.json()["username"] == "first_person"

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
        json={
            "first_name": "Second",
            "last_name": "Person",
            "username": "second_person",
            "email": "second@example.com",
            "password": "another-correct-password",
            "confirm_password": "another-correct-password",
        },
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

    settings = await client.get("/settings")
    assert settings.status_code == 200
    assert "profile-form" in settings.text


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


@pytest.mark.anyio
async def test_username_is_normalized_and_unique(client):
    payload = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "username": "Ada_Lovelace",
        "email": "ada@example.com",
        "password": "correct-horse-battery",
        "confirm_password": "correct-horse-battery",
    }
    created = await client.post("/auth/register", json=payload)
    assert created.status_code == 201
    assert created.json()["username"] == "ada_lovelace"

    duplicate = await client.post(
        "/auth/register",
        json={**payload, "email": "other@example.com", "username": "ADA_LOVELACE"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "This username is already taken"


@pytest.mark.anyio
async def test_profile_and_password_can_be_updated(client):
    registration = {
        "first_name": "Grace",
        "last_name": "Hopper",
        "username": "grace_hopper",
        "email": "grace@example.com",
        "password": "correct-horse-battery",
        "confirm_password": "correct-horse-battery",
    }
    assert (await client.post("/auth/register", json=registration)).status_code == 201

    updated = await client.patch(
        "/account/profile",
        json={"first_name": "Grace", "last_name": "Murray", "username": "grace_murray"},
    )
    assert updated.status_code == 200
    assert updated.json()["last_name"] == "Murray"
    assert updated.json()["username"] == "grace_murray"

    changed = await client.post(
        "/account/change-password",
        json={
            "current_password": "correct-horse-battery",
            "new_password": "a-brand-new-strong-password",
            "confirm_password": "a-brand-new-strong-password",
        },
    )
    assert changed.status_code == 204

    assert (await client.post("/auth/logout")).status_code == 204
    login = await client.post(
        "/auth/login",
        json={"email": "grace@example.com", "password": "a-brand-new-strong-password"},
    )
    assert login.status_code == 200
