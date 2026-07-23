from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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

    # Register fixed operational routes before the catch-all short-code route.
    setup_observability(application)
    application.include_router(health_router)
    application.mount("/static", StaticFiles(directory="static"), name="static")
    application.include_router(url_shortener_router)

    @application.get("/", response_class=FileResponse)
    def index():
        return FileResponse("static/index.html")

    return application


app = create_app()
