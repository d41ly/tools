"""add tasks.max_results

Revision ID: 0002_max_results
Revises: 0001_initial
Create Date: 2026-05-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_max_results"
down_revision: Union[str, None] = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("max_results", sa.Integer(), nullable=False, server_default="100"),
    )


def downgrade() -> None:
    op.drop_column("tasks", "max_results")
