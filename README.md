# SnapLink URL Shortener

SnapLink is a FastAPI service that creates short URLs in PostgreSQL and caches
redirect lookups in Redis. It includes Alembic migrations, health and Prometheus
endpoints, container assets, and a configurable k6 load profile.

## API

- `POST /shorten` with `{"url": "https://example.com"}` creates a short URL.
- `GET /{code}` returns a `307` redirect to the destination.
- `GET /health/live` reports whether the application process is alive.
- `GET /health/ready` checks whether required dependencies are ready.
- `GET /metrics` exposes Prometheus-format application and HTTP metrics.
- `GET /` serves the browser UI.

The URL returned by `/shorten` is built from `PUBLIC_BASE_URL`. Set it to the
externally reachable HTTPS origin in deployed environments.

## Run with Docker Compose

Copy the example configuration and start the stack:

```bash
cp .env.example .env
docker compose up --build
```

Compose starts PostgreSQL plus separate cache and rate-limit Redis instances,
waits for their health checks, applies `alembic upgrade head` in a one-shot
container, and then starts the API with four Uvicorn workers. The API is
available at `http://localhost:8000`. Database, Redis, and API ports bind only
to loopback. Compose requires both Redis roles for readiness and makes the
write limiter fail closed.

The credentials in `.env.example` are development-only. Replace them before
using the Compose file on any shared host. Stop the stack with
`docker compose down`; named PostgreSQL and Redis volumes retain data.

## Local development

Python 3.12 and running PostgreSQL and Redis instances are recommended:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
uvicorn main:app --reload
```

The application loads the project-root `.env` automatically for local
development without overriding variables already supplied by the process.

For local development, `REDIS_REQUIRED=false` allows readiness to remain useful
while Redis is temporarily unavailable. Redirects will then fall back to
PostgreSQL. Production deployments should require Redis if it is part of the
capacity plan.

## Configuration

All runtime configuration is read from environment variables:

| Variable | Example/default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg2://...` | SQLAlchemy PostgreSQL connection URL |
| `DATABASE_HOST/PORT/USER/PASSWORD/NAME` | unset | Safe alternative to an encoded `DATABASE_URL` |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Public origin used in shortened URLs |
| `DB_POOL_SIZE` | `10` | Persistent connections in each worker's pool |
| `DB_MAX_OVERFLOW` | `20` | Temporary connections above the pool size, per worker |
| `DB_POOL_TIMEOUT` | `30` | Seconds to wait for a pool connection |
| `DB_POOL_RECYCLE` | `1800` | Seconds before a pooled connection is recycled |
| `DB_CONNECT_TIMEOUT` | `2` | Fail-fast PostgreSQL connection timeout in seconds |
| `DB_HEALTH_STATEMENT_TIMEOUT_MS` | `1000` | Readiness-query timeout in milliseconds |
| `REDIS_URL` | `redis://localhost:6379/0` | Local fallback shared by both Redis roles |
| `REDIS_CACHE_URL` | falls back to `REDIS_URL` | Redirect-cache store |
| `REDIS_RATE_LIMIT_URL` | falls back to `REDIS_URL` | Distributed rate-limit store |
| `REDIS_MAX_CONNECTIONS` | `50` | Redis connection cap in each worker |
| `REDIS_CACHE_TTL` | `86400` | Redirect-cache lifetime in seconds |
| `REDIS_NEGATIVE_CACHE_TTL` | `60` | Lifetime for repeated not-found lookups |
| `REDIS_REQUIRED` | `false` locally | Whether Redis failure makes readiness fail |
| `RATE_LIMIT_ENABLED` | `true` | Enables write rate limiting |
| `RATE_LIMIT_REQUESTS` | `100` | Allowed writes in each client window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate-limit window length |
| `RATE_LIMIT_FAIL_OPEN` | `true` locally | Whether writes continue if the limiter store fails |
| `SHORT_CODE_LENGTH` | `10` | Number of characters in generated codes |
| `SHORT_CODE_MAX_ATTEMPTS` | `10` | Retries after a code collision |
| `ENVIRONMENT` | `development` | Runtime environment label |
| `WEB_CONCURRENCY` | `4` | Uvicorn worker count in the container |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Comma-separated trusted proxy IPs/CIDRs |
| `PROMETHEUS_MULTIPROC_DIR` | unset locally | Fresh, empty, writable directory for multi-worker metrics |

Pool settings apply to every worker in every replica. For example, one
four-worker replica with a pool size of 10 and maximum overflow of 20 can open
up to 120 database connections; three such replicas can open 360. Leave room
for migrations, monitoring, and operator sessions when setting PostgreSQL's
limit. Redis's cap is also per worker and per distinct server, so the default
is at most 200 connections to each Compose Redis role in one four-worker
replica. When both role URLs are identical, they reuse one process-local pool.
Compose sets PostgreSQL's development limit to
`POSTGRES_MAX_CONNECTIONS=200`.

## Migrations and production startup

Apply migrations once as a release step, before replacing application
instances:

```bash
export PROMETHEUS_MULTIPROC_DIR="$(mktemp -d)"
alembic upgrade head
uvicorn main:app --host 0.0.0.0 --port 8000 --workers "${WEB_CONCURRENCY:-4}"
```

Do not run migrations independently in every web worker. Start the service
behind a reverse proxy or load balancer that terminates TLS and checks
`/health/ready`. Set `FORWARDED_ALLOW_IPS` to only the actual proxy addresses
so Uvicorn can safely restore the original client IP used by rate limiting;
never use `*` on an untrusted network. Scrape `/metrics` with Prometheus; do
not expose that endpoint publicly unless access is controlled.

The bigint migration preserves rows from the original nullable-code schema and
uses a PostgreSQL shadow column, synchronization trigger, concurrent unique
index, and a short metadata swap. If the final lock cannot be acquired within
five seconds, the migration fails safely and can be rerun. Run it with a direct
database connection as a release operation; transaction-pooling proxies are
not suitable for `CREATE INDEX CONCURRENTLY`. Downgrading to 32-bit IDs is
rejected once any stored ID exceeds the integer range.

The Dockerfile is suitable as a deployment artifact, while `docker-compose.yml`
is a single-host development and evaluation baseline. Compose bounds cache
memory with volatile LRU eviction and keeps limiter counters in a separate
no-eviction Redis instance, so cache pressure cannot silently reset quotas.
A production topology still needs managed backups, secret injection, resource
limits, multiple instances, edge abuse protection, monitoring, and a tested
rollout and recovery procedure.

## Verification

Run the automated suite after installing `requirements-dev.txt`; tests use an
isolated in-memory database and do not require PostgreSQL or Redis:

```bash
pytest -q
```

To exercise the PostgreSQL-only online migration and invalid-index recovery,
point the destructive integration tests at an empty disposable database:

```bash
TEST_POSTGRES_URL=postgresql+psycopg2://user:password@host/test_db \
pytest -q -m postgres
```

Basic runtime checks:

```bash
curl --fail http://localhost:8000/health/live
curl --fail http://localhost:8000/health/ready
curl --fail http://localhost:8000/metrics
curl --fail --header 'Content-Type: application/json' \
  --data '{"url":"https://example.com"}' \
  http://localhost:8000/shorten
```

Readiness can fail even while liveness succeeds; that is intentional when a
required dependency is unavailable. The database probe uses its own
non-pooled, fail-fast connection, while Redis readiness exercises the actual
read, write, expiry, and script commands the service requires.

## Load testing

The k6 profile drives independent read and write arrival rates, holds a
sustained phase, ramps to a burst, then verifies recovery at the sustained
rate:

```bash
# On the isolated server under test, raise the per-generator write limit:
RATE_LIMIT_REQUESTS=10000 docker compose up --build --detach

# On the load generator:
BASE_URL=http://localhost:8000 \
READ_RATE=12 WRITE_RATE=12 \
BURST_READ_RATE=120 BURST_WRITE_RATE=120 \
k6 run load_tests/k6.js
```

Useful controls include `SUSTAINED_DURATION`, `BURST_RAMP_DURATION`,
`BURST_DURATION`, `RECOVERY_DURATION`, `POST_RECOVERY_DURATION`,
`PRE_ALLOCATED_VUS`, `MAX_VUS`, `MAX_READ_MS`, `MAX_WRITE_MS`,
`SEED_URL_COUNT`, `PRELOADED_ROW_COUNT`, and `TARGET_URL`. VU limits apply to
each scenario. The profile fails if it drops iterations or if either operation
exceeds its own error or latency budget.

The example rounds one million reads and one million writes per day up to 12
requests per second each, then applies a 10x burst. Its short sustained phase
is a smoke test, not proof of daily capacity. Use longer stages for a capacity
run, for example:

```bash
SUSTAINED_DURATION=1h \
BURST_RAMP_DURATION=2m BURST_DURATION=10m \
RECOVERY_DURATION=2m POST_RECOVERY_DURATION=10m \
k6 run load_tests/k6.js
```

Run a 6–24 hour soak before committing to a production capacity target.

The default read set is deliberately small and becomes cache-hot. To exercise
PostgreSQL and cold/mixed-cache reads, preload a production-scale dataset into
a disposable test database, then tell k6 to address those deterministic codes:

```bash
docker compose exec -T db \
  psql -U snaplink -d snaplink -v row_count=30000000 \
  < load_tests/preload.sql

PRELOADED_ROW_COUNT=30000000 \
BASE_URL=http://localhost:8000 \
k6 run load_tests/k6.js
```

Use `REDIS_CACHE_TTL=0` on the server for a database-only read profile, or
flush only the disposable benchmark Redis database immediately before a
cold-cache run.

The write limiter uses the client IP observed by the application. A proxy or
NAT can therefore make one or many generators share a limiter key and receive
`429` responses with the default limit. In an isolated performance environment,
raise `RATE_LIMIT_REQUESTS` above the planned writes per window, as above.
Keeping the limiter enabled produces a more representative Redis workload;
do not weaken production limits just to make a benchmark pass.

Run load tests against disposable data and monitor database connections,
Redis, CPU, memory, response percentiles, error rates, and dropped k6
iterations. Passing a profile demonstrates only the tested environment and
duration—it is not, by itself, a production capacity guarantee.

Short URLs are permanent in the current product contract. At one million new
links per day, plan storage and index growth for roughly 365 million rows per
year. If links should expire, define that behavior before adding a retention
job; silently deleting live redirects is not a safe infrastructure default.
# SnapLink URL Shortener

## Accounts and link privacy

Anyone can create a short link without an account. Accounts are optional and
give a signed-in user a private dashboard containing only links created while
they were signed in; the old browser-wide recent-links storage is not used.
Existing short links remain public redirects, so anyone with a short URL can
open it.

Set `SESSION_SECRET` to a long random value before deploying, and set
`SESSION_COOKIE_SECURE=true` when serving the application over HTTPS. Run the
latest database migration before starting a deployed application:

```bash
.venv/bin/python -m alembic upgrade head
```

Account API endpoints are session-cookie based:

- `POST /auth/register` creates a `free` account and starts a session.
- `POST /auth/login` starts a session.
- `POST /auth/logout` ends the session.
- `GET /auth/me` returns the signed-in account and its plan.
- `GET /account/links` returns only links owned by that account.
- `GET /account/overview` returns the plan, saved-link count, and enabled features.

Account login and registration have a separate, tighter rate limit. The plan
field supports `free` and `premium`; it defaults to `free` and cannot be
promoted by a public endpoint. Connect a payment provider/webhook before
enabling real premium upgrades.
