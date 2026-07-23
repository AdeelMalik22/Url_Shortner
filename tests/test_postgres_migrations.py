import os

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.config import get_settings


pytestmark = pytest.mark.postgres
_BASE_REVISION = "74ca9159ae4c"
_SHADOW_INDEX = "ix_urls_id_bigint_online"


def _postgres_url() -> str:
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is not set")
    return url


def _clean_database(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS urls CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        connection.execute(
            text(
                "DROP FUNCTION IF EXISTS "
                "snaplink_sync_url_bigint_id() CASCADE"
            )
        )


@pytest.fixture
def postgres_migration_database(monkeypatch):
    url = _postgres_url()
    engine = create_engine(url, poolclass=NullPool)
    config = Config("alembic.ini")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    _clean_database(engine)
    try:
        yield engine, config
    finally:
        _clean_database(engine)
        engine.dispose()
        get_settings.cache_clear()


def test_postgres_online_migration_preserves_legacy_rows(
    postgres_migration_database,
):
    engine, config = postgres_migration_database
    command.upgrade(config, _BASE_REVISION)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO urls (id, short_code, original_url)
                VALUES (-10, NULL, 'https://example.com/legacy')
                """
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT id, short_code FROM urls WHERE id = -10")
        ).one()
        data_type = connection.execute(
            text(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'urls'
                  AND column_name = 'id'
                """
            )
        ).scalar_one()

    assert row.id == -10
    assert row.short_code.startswith("L")
    assert data_type == "bigint"


def test_postgres_migration_recovers_an_invalid_concurrent_index(
    postgres_migration_database,
):
    engine, config = postgres_migration_database
    command.upgrade(config, _BASE_REVISION)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO urls (short_code, original_url)
                VALUES
                    ('legacy01', 'https://example.com/1'),
                    ('legacy02', 'https://example.com/2')
                """
            )
        )
        connection.execute(text("ALTER TABLE urls ADD COLUMN id_bigint BIGINT"))
        connection.execute(text("UPDATE urls SET id_bigint = 1"))

    with engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    f"""
                    CREATE UNIQUE INDEX CONCURRENTLY {_SHADOW_INDEX}
                    ON urls (id_bigint)
                    """
                )
            )

    with engine.begin() as connection:
        is_valid = connection.execute(
            text(
                """
                SELECT indisvalid
                FROM pg_index
                WHERE indexrelid = to_regclass(:index_name)
                """
            ),
            {"index_name": _SHADOW_INDEX},
        ).scalar_one()
        assert not is_valid
        connection.execute(text("UPDATE urls SET id_bigint = id"))

    command.upgrade(config, "head")

    with engine.connect() as connection:
        data_type = connection.execute(
            text(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'urls'
                  AND column_name = 'id'
                """
            )
        ).scalar_one()

    assert data_type == "bigint"
