"""add user profile fields

Revision ID: e5b7c9d2a310
Revises: d4f6a8b1c209
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5b7c9d2a310"
down_revision: Union[str, Sequence[str], None] = "d4f6a8b1c209"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add username as nullable first so existing accounts can be backfilled.
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "first_name",
                sa.String(length=100),
                nullable=False,
                server_default="",
            )
        )
        batch_op.add_column(
            sa.Column(
                "last_name",
                sa.String(length=100),
                nullable=False,
                server_default="",
            )
        )
        batch_op.add_column(sa.Column("username", sa.String(length=30), nullable=True))

    op.execute("UPDATE users SET username = 'user' || id WHERE username IS NULL")

    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "username",
            existing_type=sa.String(length=30),
            nullable=False,
        )
        batch_op.create_unique_constraint("uq_users_username", ["username"])
        batch_op.create_index("ix_users_username", ["username"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_username")
        batch_op.drop_constraint("uq_users_username", type_="unique")
        batch_op.drop_column("username")
        batch_op.drop_column("last_name")
        batch_op.drop_column("first_name")
