"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from sqlalchemy.engine import URL


load_dotenv(
    dotenv_path=Path(__file__).resolve().parents[1] / ".env",
    override=False,
)


class ConfigurationError(RuntimeError):
    """Raised when an environment variable contains invalid configuration."""


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(
            f"{name} environment variable is required. "
            f"Set {name} before starting the application."
        )
    return value


def _string(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    if not value:
        raise ConfigurationError(f"{name} must not be empty.")
    return value


def _integer(
    name: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default

    try:
        value = int(raw_value.strip())
    except (AttributeError, ValueError) as exc:
        raise ConfigurationError(
            f"{name} must be an integer; received {raw_value!r}."
        ) from exc

    if value < minimum:
        raise ConfigurationError(
            f"{name} must be at least {minimum}; received {value}."
        )
    if maximum is not None and value > maximum:
        raise ConfigurationError(
            f"{name} must be at most {maximum}; received {value}."
        )
    return value


def _boolean(name: str, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False

    accepted = ", ".join(sorted(_TRUE_VALUES | _FALSE_VALUES))
    raise ConfigurationError(
        f"{name} must be a boolean ({accepted}); received {raw_value!r}."
    )


def _public_base_url() -> str:
    value = _string("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    parsed = urlsplit(value)
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ConfigurationError(
            "PUBLIC_BASE_URL must contain a valid host and port."
        ) from exc

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ConfigurationError(
            "PUBLIC_BASE_URL must be an absolute HTTP or HTTPS origin."
        )
    return value


def _database_url() -> str:
    """Return a URL supplied directly or safely assemble PostgreSQL settings."""

    direct_url = os.environ.get("DATABASE_URL", "").strip()
    if direct_url:
        return direct_url

    host = os.environ.get("DATABASE_HOST", "").strip()
    if not host:
        raise ConfigurationError(
            "DATABASE_URL is required unless DATABASE_HOST, DATABASE_USER, "
            "DATABASE_PASSWORD, and DATABASE_NAME are provided."
        )

    return URL.create(
        drivername=_string("DATABASE_DRIVER", "postgresql+psycopg2"),
        username=_required("DATABASE_USER"),
        password=_required("DATABASE_PASSWORD"),
        host=host,
        port=_integer("DATABASE_PORT", 5432, maximum=65535),
        database=_required("DATABASE_NAME"),
    ).render_as_string(hide_password=False)


def _optional_string(name: str, fallback: str) -> str:
    value = os.environ.get(name)
    if value is None:
        return fallback
    normalized = value.strip()
    return normalized or fallback


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    public_base_url: str
    db_pool_size: int
    db_max_overflow: int
    db_pool_timeout: int
    db_pool_recycle: int
    db_connect_timeout: int
    db_health_statement_timeout_ms: int
    redis_url: str
    redis_cache_url: str
    redis_rate_limit_url: str
    redis_max_connections: int
    redis_cache_ttl: int
    redis_negative_cache_ttl: int
    redis_required: bool
    rate_limit_enabled: bool
    rate_limit_requests: int
    rate_limit_window_seconds: int
    rate_limit_fail_open: bool
    short_code_length: int
    short_code_max_attempts: int
    auth_required: bool
    auth_rate_limit_requests: int
    auth_rate_limit_window_seconds: int
    session_secret: str
    session_cookie_secure: bool
    session_max_age_seconds: int
    environment: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build and cache validated application settings."""

    redis_url = _string("REDIS_URL", "redis://localhost:6379/0")
    return Settings(
        database_url=_database_url(),
        public_base_url=_public_base_url(),
        db_pool_size=_integer("DB_POOL_SIZE", 10),
        db_max_overflow=_integer("DB_MAX_OVERFLOW", 20, minimum=0),
        db_pool_timeout=_integer("DB_POOL_TIMEOUT", 30),
        db_pool_recycle=_integer("DB_POOL_RECYCLE", 1800),
        db_connect_timeout=_integer("DB_CONNECT_TIMEOUT", 2),
        db_health_statement_timeout_ms=_integer(
            "DB_HEALTH_STATEMENT_TIMEOUT_MS",
            1000,
        ),
        redis_url=redis_url,
        redis_cache_url=_optional_string("REDIS_CACHE_URL", redis_url),
        redis_rate_limit_url=_optional_string(
            "REDIS_RATE_LIMIT_URL",
            redis_url,
        ),
        redis_max_connections=_integer("REDIS_MAX_CONNECTIONS", 50),
        redis_cache_ttl=_integer("REDIS_CACHE_TTL", 86400, minimum=0),
        redis_negative_cache_ttl=_integer(
            "REDIS_NEGATIVE_CACHE_TTL",
            60,
            minimum=0,
        ),
        redis_required=_boolean("REDIS_REQUIRED", False),
        rate_limit_enabled=_boolean("RATE_LIMIT_ENABLED", True),
        rate_limit_requests=_integer("RATE_LIMIT_REQUESTS", 100),
        rate_limit_window_seconds=_integer(
            "RATE_LIMIT_WINDOW_SECONDS", 60
        ),
        rate_limit_fail_open=_boolean("RATE_LIMIT_FAIL_OPEN", True),
        short_code_length=_integer(
            "SHORT_CODE_LENGTH",
            10,
            minimum=6,
            maximum=32,
        ),
        short_code_max_attempts=_integer("SHORT_CODE_MAX_ATTEMPTS", 10),
        auth_required=_boolean("AUTH_REQUIRED", False),
        auth_rate_limit_requests=_integer(
            "AUTH_RATE_LIMIT_REQUESTS", 10, minimum=1
        ),
        auth_rate_limit_window_seconds=_integer(
            "AUTH_RATE_LIMIT_WINDOW_SECONDS", 300, minimum=1
        ),
        session_secret=_required("SESSION_SECRET"),
        session_cookie_secure=_boolean("SESSION_COOKIE_SECURE", False),
        session_max_age_seconds=_integer("SESSION_MAX_AGE_SECONDS", 604800),
        environment=_string("ENVIRONMENT", "development").lower(),
    )
