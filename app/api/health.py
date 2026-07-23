from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette import status
from starlette.responses import JSONResponse

from app.config import get_settings
from app.services.cache import reset_cache_resilience_state
from app.services.rate_limiter import reset_rate_limiter_resilience_state
from app.utils.db_connection import health_engine
from app.utils.redis_client import (
    get_cache_redis_client,
    get_rate_limit_redis_client,
    redis_capabilities_ready,
)


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def readiness() -> JSONResponse:
    settings = get_settings()

    try:
        with health_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        database_ready = True
    except (SQLAlchemyError, OSError):
        database_ready = False

    try:
        cache_redis_ready = redis_capabilities_ready(
            get_cache_redis_client()
        )
    except (OSError, ValueError):
        cache_redis_ready = False
    if settings.redis_cache_url == settings.redis_rate_limit_url:
        rate_limit_redis_ready = cache_redis_ready
    else:
        try:
            rate_limit_redis_ready = redis_capabilities_ready(
                get_rate_limit_redis_client()
            )
        except (OSError, ValueError):
            rate_limit_redis_ready = False

    if cache_redis_ready:
        reset_cache_resilience_state()
    if rate_limit_redis_ready:
        reset_rate_limiter_resilience_state()
    redis_ready = cache_redis_ready and rate_limit_redis_ready

    ready = database_ready and (redis_ready or not settings.redis_required)
    if ready and redis_ready:
        service_status = "ok"
    elif ready:
        service_status = "degraded"
    else:
        service_status = "unavailable"

    return JSONResponse(
        status_code=(
            status.HTTP_200_OK
            if ready
            else status.HTTP_503_SERVICE_UNAVAILABLE
        ),
        content={
            "status": service_status,
            "checks": {
                "database": "ok" if database_ready else "unavailable",
                "redis": "ok" if redis_ready else "unavailable",
                "redis_cache": (
                    "ok" if cache_redis_ready else "unavailable"
                ),
                "redis_rate_limit": (
                    "ok" if rate_limit_redis_ready else "unavailable"
                ),
            },
        },
    )
