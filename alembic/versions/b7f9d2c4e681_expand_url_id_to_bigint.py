"""expand URL IDs to bigint

Revision ID: b7f9d2c4e681
Revises: 74ca9159ae4c
Create Date: 2026-07-23

"""
from __future__ import annotations

import hashlib
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7f9d2c4e681"
down_revision: Union[str, Sequence[str], None] = "74ca9159ae4c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEGACY_BATCH_SIZE = 1_000
_BIGINT_BATCH_SIZE = 10_000
_POSTGRES_FINAL_LOCK_TIMEOUT = "5s"
_POSTGRES_DOWNGRADE_TIMEOUT = "5min"
_POSTGRES_INTEGER_MIN = -2_147_483_648
_POSTGRES_INTEGER_MAX = 2_147_483_647
_SHADOW_COLUMN = "id_bigint"
_SHADOW_INDEX = "ix_urls_id_bigint_online"
_NOT_NULL_CHECK = "ck_urls_id_bigint_not_null"
_SHORT_CODE_NOT_NULL_CHECK = "ck_urls_short_code_not_null"
_SYNC_FUNCTION = "snaplink_sync_url_bigint_id"
_SYNC_TRIGGER = "trg_snaplink_sync_url_bigint_id"


def _legacy_code(row_id: int, attempt: int) -> str:
    digest = hashlib.sha256(
        f"snaplink-legacy-url:{row_id}:{attempt}".encode()
    ).hexdigest()
    return f"L{digest[:30]}"


def _backfill_null_short_codes(bind) -> None:
    """Preserve rows created while the original schema allowed NULL codes."""

    if context.is_offline_mode():
        if bind.dialect.name == "postgresql":
            op.execute(
                """
                UPDATE urls
                SET short_code =
                    'L' || substr(md5('snaplink-legacy-url:' || id::text), 1, 30)
                WHERE short_code IS NULL
                """
            )
        return

    urls = sa.table(
        "urls",
        sa.column("id", sa.Integer()),
        sa.column("short_code", sa.String()),
    )

    while True:
        row_ids = list(
            bind.execute(
                sa.select(urls.c.id)
                .where(urls.c.short_code.is_(None))
                .order_by(urls.c.id)
                .limit(_LEGACY_BATCH_SIZE)
            ).scalars()
        )
        if not row_ids:
            return

        for row_id in row_ids:
            for attempt in range(100):
                candidate = _legacy_code(int(row_id), attempt)
                exists = bind.execute(
                    sa.select(urls.c.id)
                    .where(urls.c.short_code == candidate)
                    .limit(1)
                ).first()
                if exists is None:
                    bind.execute(
                        sa.update(urls)
                        .where(urls.c.id == row_id)
                        .where(urls.c.short_code.is_(None))
                        .values(short_code=candidate)
                    )
                    break
            else:
                raise RuntimeError(
                    f"Unable to backfill a unique short code for URL row {row_id}"
                )


def _backfill_bigint_ids(bind) -> None:
    """Copy IDs in bounded transactions while the synchronization trigger runs."""

    last_id = _POSTGRES_INTEGER_MIN - 1
    statement = sa.text(
        f"""
        WITH batch AS (
            SELECT id
            FROM urls
            WHERE id > :last_id
              AND {_SHADOW_COLUMN} IS NULL
            ORDER BY id
            LIMIT :batch_size
        )
        UPDATE urls AS target
        SET {_SHADOW_COLUMN} = target.id::bigint
        FROM batch
        WHERE target.id = batch.id
        RETURNING target.id
        """
    )

    while True:
        updated_ids = list(
            bind.execute(
                statement,
                {
                    "last_id": last_id,
                    "batch_size": _BIGINT_BATCH_SIZE,
                },
            ).scalars()
        )
        if not updated_ids:
            return
        last_id = max(updated_ids)


def _prepare_shadow_index(bind) -> None:
    """Reuse a valid shadow index or recover one left invalid by interruption."""

    usable = bind.execute(
        sa.text(
            f"""
            SELECT (
                index_data.indisvalid
                AND index_data.indisready
                AND index_data.indisunique
                AND index_data.indpred IS NULL
                AND index_data.indexprs IS NULL
                AND index_data.indnatts = 1
                AND index_data.indnkeyatts = 1
                AND index_data.indkey[0] = column_data.attnum
                AND index_class.relam = (
                    SELECT oid FROM pg_am WHERE amname = 'btree'
                )
            ) AS usable
            FROM pg_class AS index_class
            JOIN pg_index AS index_data
              ON index_data.indexrelid = index_class.oid
            JOIN pg_attribute AS column_data
              ON column_data.attrelid = index_data.indrelid
             AND column_data.attname = '{_SHADOW_COLUMN}'
             AND NOT column_data.attisdropped
            WHERE index_class.oid = to_regclass(:index_name)
              AND index_data.indrelid = 'urls'::regclass
            """
        ),
        {"index_name": _SHADOW_INDEX},
    ).scalar_one_or_none()

    if usable is not None and not usable:
        op.execute(f"DROP INDEX CONCURRENTLY {_SHADOW_INDEX}")
        usable = None

    if usable is None:
        op.execute(
            f"""
            CREATE UNIQUE INDEX CONCURRENTLY {_SHADOW_INDEX}
            ON urls ({_SHADOW_COLUMN})
            """
        )


def _postgresql_online_upgrade() -> None:
    """Expand, backfill, index, and swap the primary key in committed phases."""

    # Each statement in this block commits independently. Consequently, the
    # quick ACCESS EXCLUSIVE locks needed by ALTER TABLE are never retained
    # through either data backfill.
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        op.execute(
            f"SET lock_timeout = '{_POSTGRES_FINAL_LOCK_TIMEOUT}'"
        )
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'urls'::regclass
                      AND conname = '{_SHORT_CODE_NOT_NULL_CHECK}'
                ) THEN
                    ALTER TABLE urls
                    ADD CONSTRAINT {_SHORT_CODE_NOT_NULL_CHECK}
                    CHECK (short_code IS NOT NULL) NOT VALID;
                END IF;
            END
            $$
            """
        )
        # The trigger keeps the shadow column synchronized while the committed
        # batches and concurrent index build run alongside normal traffic.
        op.execute(
            f"ALTER TABLE urls ADD COLUMN IF NOT EXISTS {_SHADOW_COLUMN} BIGINT"
        )
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION {_SYNC_FUNCTION}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                NEW.{_SHADOW_COLUMN} := NEW.id::bigint;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(f"DROP TRIGGER IF EXISTS {_SYNC_TRIGGER} ON urls")
        op.execute(
            f"""
            CREATE TRIGGER {_SYNC_TRIGGER}
            BEFORE INSERT OR UPDATE OF id ON urls
            FOR EACH ROW EXECUTE FUNCTION {_SYNC_FUNCTION}()
            """
        )
        op.execute("RESET lock_timeout")
        _backfill_null_short_codes(bind)
        _backfill_bigint_ids(bind)

        op.execute(
            f"SET lock_timeout = '{_POSTGRES_FINAL_LOCK_TIMEOUT}'"
        )
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'urls'::regclass
                      AND conname = '{_NOT_NULL_CHECK}'
                ) THEN
                    ALTER TABLE urls
                    ADD CONSTRAINT {_NOT_NULL_CHECK}
                    CHECK ({_SHADOW_COLUMN} IS NOT NULL) NOT VALID;
                END IF;
            END
            $$
            """
        )
        op.execute(
            f"ALTER TABLE urls VALIDATE CONSTRAINT "
            f"{_SHORT_CODE_NOT_NULL_CHECK}"
        )
        op.execute("ALTER TABLE urls ALTER COLUMN short_code SET NOT NULL")
        op.execute(
            f"ALTER TABLE urls DROP CONSTRAINT {_SHORT_CODE_NOT_NULL_CHECK}"
        )
        op.execute(
            f"ALTER TABLE urls VALIDATE CONSTRAINT {_NOT_NULL_CHECK}"
        )
        op.execute(
            f"ALTER TABLE urls ALTER COLUMN {_SHADOW_COLUMN} SET NOT NULL"
        )
        op.execute("RESET lock_timeout")
        _prepare_shadow_index(bind)

    # The final column/constraint swap is metadata-only. Fail quickly if another
    # transaction prevents acquiring the exclusive lock instead of stalling a
    # deployment indefinitely.
    op.execute(
        f"SET LOCAL lock_timeout = '{_POSTGRES_FINAL_LOCK_TIMEOUT}'"
    )
    op.execute("LOCK TABLE urls IN ACCESS EXCLUSIVE MODE")
    op.execute("ALTER SEQUENCE IF EXISTS urls_id_seq OWNED BY NONE")
    op.execute("ALTER TABLE urls DROP CONSTRAINT urls_pkey")
    op.execute("ALTER TABLE urls ALTER COLUMN id DROP DEFAULT")
    op.execute("ALTER TABLE urls RENAME COLUMN id TO id_integer_old")
    op.execute(
        f"ALTER TABLE urls RENAME COLUMN {_SHADOW_COLUMN} TO id"
    )
    op.execute(
        f"""
        ALTER TABLE urls
        ADD CONSTRAINT urls_pkey PRIMARY KEY USING INDEX {_SHADOW_INDEX}
        """
    )
    op.execute(
        """
        ALTER TABLE urls
        ALTER COLUMN id SET DEFAULT nextval('urls_id_seq'::regclass)
        """
    )
    op.execute("ALTER SEQUENCE IF EXISTS urls_id_seq AS BIGINT")
    op.execute("ALTER SEQUENCE IF EXISTS urls_id_seq OWNED BY urls.id")
    op.execute(f"DROP TRIGGER IF EXISTS {_SYNC_TRIGGER} ON urls")
    op.execute(f"DROP FUNCTION IF EXISTS {_SYNC_FUNCTION}()")
    op.execute(f"ALTER TABLE urls DROP CONSTRAINT {_NOT_NULL_CHECK}")
    op.execute("ALTER TABLE urls DROP COLUMN id_integer_old")


def _direct_upgrade(bind) -> None:
    op.drop_index(op.f("ix_urls_id"), table_name="urls")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("urls") as batch_op:
            batch_op.alter_column(
                "short_code",
                existing_type=sa.String(),
                nullable=False,
            )
            batch_op.alter_column(
                "id",
                existing_type=sa.Integer(),
                type_=sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                existing_nullable=False,
            )
        return

    op.alter_column(
        "urls",
        "short_code",
        existing_type=sa.String(),
        nullable=False,
    )
    alter_options = {}
    if bind.dialect.name == "postgresql":
        alter_options["postgresql_using"] = "id::bigint"
    op.alter_column(
        "urls",
        "id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
        **alter_options,
    )
    if bind.dialect.name == "postgresql":
        op.execute("ALTER SEQUENCE IF EXISTS urls_id_seq AS BIGINT")


def upgrade() -> None:
    """Use 64-bit IDs, preserve legacy rows, and remove the redundant ID index."""

    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        if context.is_offline_mode():
            raise RuntimeError(
                "The PostgreSQL bigint migration requires an online database "
                "connection so backfill batches can commit independently."
            )
        _postgresql_online_upgrade()
    else:
        _backfill_null_short_codes(bind)
        _direct_upgrade(bind)


def _guard_postgresql_downgrade(bind) -> None:
    if context.is_offline_mode():
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM urls
                    WHERE id < {_POSTGRES_INTEGER_MIN}
                       OR id > {_POSTGRES_INTEGER_MAX}
                ) THEN
                    RAISE EXCEPTION
                        'Cannot downgrade urls.id: values exceed INTEGER range';
                END IF;
            END
            $$
            """
        )
        return

    smallest_id, largest_id = bind.execute(
        sa.text("SELECT min(id), max(id) FROM urls")
    ).one()
    if (
        smallest_id is not None
        and (
            smallest_id < _POSTGRES_INTEGER_MIN
            or largest_id > _POSTGRES_INTEGER_MAX
        )
    ):
        raise RuntimeError(
            "Cannot downgrade urls.id to INTEGER because stored IDs exceed "
            f"the range {_POSTGRES_INTEGER_MIN}..{_POSTGRES_INTEGER_MAX}."
        )


def downgrade() -> None:
    """Restore the original 32-bit ID only when every value fits safely."""

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _guard_postgresql_downgrade(bind)
        if not context.is_offline_mode():
            op.execute(
                f"SET LOCAL lock_timeout = '{_POSTGRES_FINAL_LOCK_TIMEOUT}'"
            )
            op.execute(
                f"SET LOCAL statement_timeout = '{_POSTGRES_DOWNGRADE_TIMEOUT}'"
            )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("urls") as batch_op:
            batch_op.alter_column(
                "short_code",
                existing_type=sa.String(),
                nullable=True,
            )
            batch_op.alter_column(
                "id",
                existing_type=sa.BigInteger().with_variant(
                    sa.Integer(), "sqlite"
                ),
                type_=sa.Integer(),
                existing_nullable=False,
            )
    else:
        op.alter_column(
            "urls",
            "short_code",
            existing_type=sa.String(),
            nullable=True,
        )
        alter_options = {}
        if bind.dialect.name == "postgresql":
            alter_options["postgresql_using"] = "id::integer"
        op.alter_column(
            "urls",
            "id",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
            **alter_options,
        )

    if bind.dialect.name == "postgresql":
        op.execute("ALTER SEQUENCE IF EXISTS urls_id_seq AS INTEGER")

    op.create_index(op.f("ix_urls_id"), "urls", ["id"], unique=False)
