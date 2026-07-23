# CLAUDE.md

## Project overview

SnapLink is a small FastAPI URL-shortening service. It stores URL mappings in
PostgreSQL with SQLAlchemy, manages schema changes with Alembic, and serves a
single-page frontend from `static/index.html`.

## Repository map

- `main.py`: creates the FastAPI app, mounts static files, and registers routes.
- `app/api/v1/url_shortner/router.py`: shortening and redirect HTTP endpoints.
- `app/api/v1/url_shortner/schema.py`: Pydantic request and response models.
- `app/api/v1/url_shortner/models.py`: SQLAlchemy URL model.
- `app/services/url_shortner.py`: URL creation and persistence logic.
- `app/utils/common.py`: short-code generation.
- `app/utils/db_connection.py`: database engine, session, and declarative base.
- `alembic/`: database migrations.
- `static/index.html`: browser UI, styles, and client-side behavior.

## Local development

Create and activate a virtual environment, install dependencies, migrate the
database, and start the development server:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn main:app --reload
```

The app is available at `http://localhost:8000`.

Database settings currently exist in both `app/utils/db_connection.py` and
`alembic.ini`. Keep them synchronized when changing local database
configuration. Do not commit real production credentials.

## API contract

- `POST /shorten` accepts `{"url": "https://example.com"}` and returns
  `{"short_url": "http://localhost:8000/<code>"}`.
- `GET /<code>` redirects to the stored original URL.
- `GET /` serves the frontend.

Preserve the `short_url` response key because the frontend depends on it.

## Development guidelines

- Keep HTTP validation and responses in the router.
- Keep database operations and business logic in the service layer.
- Use the `get_db` dependency for request-scoped SQLAlchemy sessions.
- Define request and response shapes with Pydantic models.
- Create an Alembic migration for every intentional database schema change.
- Keep `requirements.txt` synchronized with imported third-party packages.
- Preserve unrelated worktree changes and do not commit generated files,
  virtual environments, local environment files, or IDE metadata.
- Never expose database credentials, secrets, or internal errors in API
  responses.

## Verification

There is no automated test suite yet. For backend changes, at minimum:

1. Confirm the application imports successfully.
2. Run pending Alembic migrations against a development database.
3. Exercise URL creation and redirect behavior.
4. Check invalid URLs and unknown short codes.

When adding tests, place them under `tests/` and use FastAPI's test client with
an isolated test database.
