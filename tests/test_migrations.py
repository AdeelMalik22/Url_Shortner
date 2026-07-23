import sqlite3

from alembic import command
from alembic.config import Config

from app.config import get_settings


def test_upgrade_preserves_legacy_rows_with_null_codes(tmp_path, monkeypatch):
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    alembic_config = Config("alembic.ini")

    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        command.upgrade(alembic_config, "74ca9159ae4c")
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "INSERT INTO urls (short_code, original_url) VALUES (?, ?)",
                (None, "https://example.com/legacy"),
            )

        command.upgrade(alembic_config, "head")

        with sqlite3.connect(database_path) as connection:
            row = connection.execute(
                "SELECT short_code, original_url FROM urls"
            ).fetchone()
            columns = {
                info[1]: info
                for info in connection.execute("PRAGMA table_info(urls)")
            }

        assert row is not None
        assert row[0].startswith("L")
        assert len(row[0]) == 31
        assert row[1] == "https://example.com/legacy"
        assert columns["short_code"][3] == 1

        command.downgrade(alembic_config, "74ca9159ae4c")
        command.upgrade(alembic_config, "head")
    finally:
        get_settings.cache_clear()
