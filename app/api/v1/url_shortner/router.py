from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette import status
from starlette.responses import FileResponse, RedirectResponse

from app.api.auth import get_optional_user
from app.api.v1.url_shortner.models import URL, User
from app.api.v1.url_shortner.schema import URLCreate, URLResponse
from app.config import get_settings
from app.services.cache import (
    cache_missing_url,
    cache_url,
    get_cached_url,
    invalidate_cached_url,
)
from app.services.rate_limiter import check_rate_limit
from app.services.url_shortner import (
    ShortCodeAllocationError,
    create_short_url,
)
from app.utils.db_connection import get_db


router = APIRouter()


def _client_id(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host


@router.get("/register", include_in_schema=False)
@router.get("/login", include_in_schema=False)
def account_page() -> FileResponse:
    """Reserve account paths so they can never be interpreted as short codes."""
    return FileResponse("static/auth.html")


@router.get("/settings", include_in_schema=False)
def settings_page() -> FileResponse:
    """Reserve settings so it can never be interpreted as a short code."""
    return FileResponse("static/settings.html")


@router.post("/shorten", response_model=URLResponse)
def shorten(
    data: URLCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
) -> URLResponse:
    settings = get_settings()
    if settings.auth_required and current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to shorten links")
    decision = check_rate_limit(_client_id(request), "shorten")

    rate_limit_headers = {}
    if decision.enabled and decision.available:
        rate_limit_headers = {
            "X-RateLimit-Limit": str(settings.rate_limit_requests),
            "X-RateLimit-Remaining": str(decision.remaining),
        }

    if not decision.available and not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate-limit service unavailable; please retry",
            headers={"Retry-After": str(decision.retry_after)},
        )

    if not decision.allowed:
        rate_limit_headers["Retry-After"] = str(decision.retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many shortening requests",
            headers=rate_limit_headers,
        )

    if rate_limit_headers:
        response.headers.update(rate_limit_headers)

    try:
        url = create_short_url(db, str(data.url), user_id=current_user.id if current_user else None)
    except ShortCodeAllocationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not allocate a short URL; please retry",
        ) from exc

    # Clear the vanishingly rare case where this not-yet-created code was
    # previously negative-cached after a speculative lookup.
    invalidate_cached_url(url.short_code)

    return URLResponse(
        short_url=f"{settings.public_base_url.rstrip('/')}/{url.short_code}"
    )


@router.get("/{code}")
def redirect(
    code: Annotated[
        str,
        Path(
            min_length=6,
            max_length=32,
            pattern=r"^[A-Za-z0-9]+$",
        ),
    ],
    db: Session = Depends(get_db),
) -> RedirectResponse:
    cached = get_cached_url(code)
    if cached.hit:
        if cached.original_url is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="URL not found",
            )
        original_url = cached.original_url
    else:
        try:
            original_url = db.scalar(
                select(URL.original_url).where(URL.short_code == code)
            )
        finally:
            # Release the read transaction before a potentially slow cache
            # write, rather than holding a pooled database connection.
            db.rollback()

        if original_url is None:
            cache_missing_url(code)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="URL not found",
            )

        cache_url(code, original_url)

    return RedirectResponse(
        url=original_url,
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
