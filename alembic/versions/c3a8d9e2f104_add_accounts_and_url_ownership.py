"""add accounts and URL ownership

Revision ID: c3a8d9e2f104
Revises: b7f9d2c4e681
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3a8d9e2f104"
down_revision: Union[str, Sequence[str], None] = "b7f9d2c4e681"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    with op.batch_alter_table("urls") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.BigInteger(), nullable=True))
        batch_op.create_foreign_key("fk_urls_user_id", "users", ["user_id"], ["id"], ondelete="SET NULL")
        batch_op.create_index("ix_urls_user_id", ["user_id"])


def downgrade() -> None:
    with op.batch_alter_table("urls") as batch_op:
        batch_op.drop_index("ix_urls_user_id")
        batch_op.drop_constraint("fk_urls_user_id", type_="foreignkey")
        batch_op.drop_column("user_id")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
