import logging
import os
from pathlib import Path
from threading import Lock
from time import perf_counter
from time import monotonic

from fastapi import FastAPI, Request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    multiprocess,
)
from starlette.responses import Response


logger = logging.getLogger(__name__)
_METRICS_WARNING_INTERVAL_SECONDS = 60.0
_metrics_warning_lock = Lock()
_last_metrics_warning_at = float("-inf")


def _prepare_multiprocess_directory() -> Path | None:
    """Create and verify the Prometheus directory before serving traffic."""

    configured = os.getenv("PROMETHEUS_MULTIPROC_DIR")
    if not configured:
        return None

    directory = Path(configured)
    probe = directory / f".snaplink-write-test-{os.getpid()}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if not directory.is_dir():
            raise NotADirectoryError(str(directory))
        probe.touch(exist_ok=False)
        probe.unlink()
    except OSError as exc:
        raise RuntimeError(
            "PROMETHEUS_MULTIPROC_DIR must be a writable directory: "
            f"{directory}"
        ) from exc
    return directory


_MULTIPROCESS_DIRECTORY = _prepare_multiprocess_directory()


HTTP_REQUESTS = Counter(
    "snaplink_http_requests_total",
    "Total HTTP requests handled by SnapLink.",
    ("method", "route", "status_code"),
)

HTTP_REQUEST_DURATION = Histogram(
    "snaplink_http_request_duration_seconds",
    "SnapLink HTTP request duration in seconds.",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", "unmatched")


def _metrics_registry():
    if _MULTIPROCESS_DIRECTORY is None:
        return REGISTRY

    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return registry


def _record_request_metrics(
    request: Request,
    *,
    status_code: int,
    duration: float,
) -> None:
    """Record metrics without allowing an observability failure to break HTTP."""

    global _last_metrics_warning_at

    try:
        route = _route_label(request)
        HTTP_REQUESTS.labels(
            method=request.method,
            route=route,
            status_code=str(status_code),
        ).inc()
        HTTP_REQUEST_DURATION.labels(
            method=request.method,
            route=route,
        ).observe(duration)
    except Exception:
        now = monotonic()
        with _metrics_warning_lock:
            should_log = (
                now - _last_metrics_warning_at
                >= _METRICS_WARNING_INTERVAL_SECONDS
            )
            if should_log:
                _last_metrics_warning_at = now
        if should_log:
            logger.exception("Unable to record HTTP request metrics")


def setup_observability(app: FastAPI) -> None:
    @app.middleware("http")
    async def observe_request(request: Request, call_next):
        started_at = perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            _record_request_metrics(
                request,
                status_code=status_code,
                duration=perf_counter() - started_at,
            )

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(
            content=generate_latest(_metrics_registry()),
            media_type=CONTENT_TYPE_LATEST,
        )
