import pytest
from sqlalchemy.engine import make_url

from app import config


def test_database_url_is_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_HOST", raising=False)

    with pytest.raises(config.ConfigurationError, match="DATABASE_URL"):
        config._database_url()


def test_database_url_can_be_safely_assembled(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DATABASE_HOST", "db")
    monkeypatch.setenv("DATABASE_USER", "snap@user")
    monkeypatch.setenv("DATABASE_PASSWORD", "p@ss:/?#word")
    monkeypatch.setenv("DATABASE_NAME", "snaplink")

    parsed = make_url(config._database_url())

    assert parsed.username == "snap@user"
    assert parsed.password == "p@ss:/?#word"
    assert parsed.host == "db"


@pytest.mark.parametrize(
    "value",
    (
        "/relative",
        "ftp://example.com",
        "https://user:password@example.com",
        "https://example.com?query=value",
        "https://example.com/prefix",
        "http://:8000",
        "http://example.com:not-a-port",
    ),
)
def test_public_base_url_must_be_safe_and_absolute(monkeypatch, value):
    monkeypatch.setenv("PUBLIC_BASE_URL", value)

    with pytest.raises(config.ConfigurationError, match="PUBLIC_BASE_URL"):
        config._public_base_url()


@pytest.mark.parametrize("value", ("5", "33"))
def test_short_code_length_must_match_route_bounds(monkeypatch, value):
    monkeypatch.setenv("SHORT_CODE_LENGTH", value)

    with pytest.raises(config.ConfigurationError, match="SHORT_CODE_LENGTH"):
        config._integer(
            "SHORT_CODE_LENGTH",
            10,
            minimum=6,
            maximum=32,
        )
