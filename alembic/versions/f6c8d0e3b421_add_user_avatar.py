"""add user avatar

Revision ID: f6c8d0e3b421
Revises: e5b7c9d2a310
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6c8d0e3b421"
down_revision: Union[str, Sequence[str], None] = "e5b7c9d2a310"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("avatar_filename", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("avatar_filename")
