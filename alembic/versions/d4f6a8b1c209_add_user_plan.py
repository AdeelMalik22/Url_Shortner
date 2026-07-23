"""add user plan

Revision ID: d4f6a8b1c209
Revises: c3a8d9e2f104
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4f6a8b1c209"
down_revision: Union[str, Sequence[str], None] = "c3a8d9e2f104"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "plan",
                sa.String(length=16),
                nullable=False,
                server_default="free",
            )
        )
        batch_op.create_check_constraint(
            "ck_users_plan",
            "plan IN ('free', 'premium')",
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_plan", type_="check")
        batch_op.drop_column("plan")
