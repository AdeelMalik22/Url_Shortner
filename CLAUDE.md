# CLAUDE.md

## Project overview

SnapLink is a FastAPI URL-shortening service. PostgreSQL is the source of truth,
Redis accelerates redirects and backs shared rate limiting, Alembic owns schema
changes, and `static/index.html` provides the browser UI.

## Repository map

- `main.py`: application factory, lifespan, static files, and router setup.
- `app/api/health.py`: liveness and dependency readiness routes.
- `app/observability.py`: HTTP instrumentation and Prometheus endpoint.
- `app/api/v1/url_shortner/router.py`: shortening and redirect endpoints.
- `app/api/v1/url_shortner/schema.py`: Pydantic request and response models.
- `app/api/v1/url_shortner/models.py`: SQLAlchemy URL model.
- `app/services/url_shortner.py`: URL persistence and collision retry.
- `app/services/cache.py`: best-effort Redis redirect caching.
- `app/services/rate_limiter.py`: Redis-backed distributed write limiting.
- `app/utils/common.py`: short-code generation.
- `app/utils/db_connection.py`: environment-backed engine and session setup.
- `app/utils/redis_client.py`: process-local Redis client construction.
- `alembic/`: database migrations; never replace these with import-time DDL.
- `tests/`: automated tests.
- `load_tests/k6.js`: sustained and burst read/write load profile.
- `Dockerfile` and `docker-compose.yml`: container and local stack assets.

## Environment and local setup

Start from the checked-in template; never commit `.env`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
uvicorn main:app --reload
```

The application loads the root `.env` with `override=False`; explicit process
environment variables always win.

Important variables are `DATABASE_URL`, `PUBLIC_BASE_URL`, the `DB_POOL_*`
settings, `DB_CONNECT_TIMEOUT`, `DB_HEALTH_STATEMENT_TIMEOUT_MS`, `REDIS_URL`,
`REDIS_CACHE_URL`, `REDIS_RATE_LIMIT_URL`, `REDIS_MAX_CONNECTIONS`,
`REDIS_CACHE_TTL`, `REDIS_NEGATIVE_CACHE_TTL`, `REDIS_REQUIRED`, all
`RATE_LIMIT_*` settings, `SHORT_CODE_LENGTH`, `SHORT_CODE_MAX_ATTEMPTS`,
`ENVIRONMENT`, `WEB_CONCURRENCY`, and `FORWARDED_ALLOW_IPS`. Prometheus
multiprocess mode also needs `PROMETHEUS_MULTIPROC_DIR` to identify a fresh,
empty, writable directory; do not set it for the single-worker reload server.

For an integrated local stack:

```bash
cp .env.example .env
docker compose up --build
```

Compose health-gates PostgreSQL and separate cache/limiter Redis services, runs
`alembic upgrade head` once, then starts four Uvicorn workers. Its checked-in
passwords are examples only, and published ports bind to loopback.

## API contract

- `POST /shorten` accepts `{"url": "https://example.com"}` and returns
  `{"short_url": "<PUBLIC_BASE_URL>/<code>"}`.
- `GET /{code}` returns an HTTP redirect or `404`.
- `GET /health/live` checks process liveness.
- `GET /health/ready` checks required dependencies.
- `GET /metrics` returns Prometheus-format metrics.
- `GET /` serves the frontend.

Preserve the `short_url` response key because the frontend and load test use
it. Redirect tests should disable redirect following so they do not call
third-party destination URLs.

## Database, cache, and workers

- Use `get_db` for request-scoped SQLAlchemy sessions.
- Treat PostgreSQL as authoritative and Redis as an optimization.
- Use `REDIS_REQUIRED` to decide readiness. Redirect cache failures fall back to
  PostgreSQL, while limiter failures follow `RATE_LIMIT_FAIL_OPEN`.
- Keep production cache and limiter Redis roles separate so cache eviction
  cannot reset quota counters. Matching URLs intentionally share one pool for
  local development.
- PostgreSQL and Redis pool limits are per process. Check the total across
  every worker and replica, plus operational headroom, against server limits.
- Trust proxy headers only from known proxy addresses; rate limiting depends on
  the client IP reconstructed by Uvicorn.
- Generate schema changes with Alembic and apply `alembic upgrade head` as a
  release step before web processes start.
- Never call `Base.metadata.create_all()` during application import.
- With multiple workers, keep Prometheus's multiprocess directory shared by
  workers in one application instance and empty it before a fresh instance
  starts.

## Development guidelines

- Keep HTTP validation and responses in the router and persistence/business
  rules in the service layer.
- Retry short-code uniqueness conflicts with a fresh transaction state; do not
  turn integrity errors into generic `500` responses.
- Use environment variables for credentials and deployment-specific values.
- Keep `requirements.txt` synchronized with imported packages.
- Preserve unrelated worktree changes and generated migration history.
- Do not expose database credentials, internal errors, `/metrics`, or trusted
  proxy assumptions publicly.

## Verification

Run:

```bash
pytest -q
alembic upgrade head
```

Then exercise `/shorten`, a redirect with redirect following disabled, invalid
URLs, missing codes, `/health/live`, `/health/ready`, and `/metrics`.

For load testing:

```bash
RATE_LIMIT_REQUESTS=10000 docker compose up --build --detach
BASE_URL=http://localhost:8000 \
READ_RATE=12 WRITE_RATE=12 \
BURST_READ_RATE=120 BURST_WRITE_RATE=120 \
k6 run load_tests/k6.js
```

Raise `RATE_LIMIT_REQUESTS` for the load generator; keep limiting enabled when
possible so Redis work remains representative. Use `load_tests/preload.sql`
with `PRELOADED_ROW_COUNT` for large cold/mixed-cache tests, and use long
sustained and soak durations for capacity claims. Record the tested revision,
topology, dataset size, durations, rates, latency percentiles, errors, dropped
iterations, and dependency saturation. Never infer production capacity from
an unrecorded or materially different load profile.
