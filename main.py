from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.auth import router as auth_router
from app.config import get_settings
from app.api.health import router as health_router
from app.api.v1.url_shortner.router import router as url_shortener_router
from app.observability import setup_observability
from app.services.cache import reset_cache_resilience_state
from app.services.rate_limiter import reset_rate_limiter_resilience_state
from app.utils.db_connection import engine, health_engine
from app.utils.redis_client import clear_redis_client_cache


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    reset_cache_resilience_state()
    reset_rate_limiter_resilience_state()
    clear_redis_client_cache()
    engine.dispose()
    health_engine.dispose()


def create_app() -> FastAPI:
    application = FastAPI(
        title="SnapLink URL Shortener",
        lifespan=lifespan,
    )
    settings = get_settings()
    application.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        max_age=settings.session_max_age_seconds,
        https_only=settings.session_cookie_secure,
        same_site="lax",
    )

    # Register fixed operational routes before the catch-all short-code route.
    setup_observability(application)
    application.include_router(health_router)
    application.include_router(auth_router)
    application.mount("/static", StaticFiles(directory="static"), name="static")

    @application.get("/", response_class=FileResponse)
    def index():
        return FileResponse("static/index.html")

    @application.get("/register", include_in_schema=False, response_class=FileResponse)
    @application.get("/login", include_in_schema=False, response_class=FileResponse)
    def account_entry():
        """Serve a focused account page instead of putting auth in the shortener."""
        return FileResponse("static/auth.html")

    @application.get("/settings", include_in_schema=False, response_class=FileResponse)
    def settings_page():
        return FileResponse("static/settings.html")

    @application.get("/dashboard", include_in_schema=False, response_class=FileResponse)
    def dashboard_page():
        return FileResponse("static/dashboard.html")

    @application.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        """Avoid sending the browser's favicon request to the short-code route."""
        return Response(status_code=204)

    application.include_router(url_shortener_router)

    return application


app = create_app()
