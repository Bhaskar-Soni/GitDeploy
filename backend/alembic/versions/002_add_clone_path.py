"""Add clone_path to jobs table

Revision ID: 002
Revises: 001
Create Date: 2026-03-28 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("clone_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "clone_path")
