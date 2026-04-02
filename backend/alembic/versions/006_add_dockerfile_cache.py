"""Add dockerfile_cache table for self-learning.

Revision ID: 006
Revises: 005
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dockerfile_cache",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("stack_signature", sa.Text(), nullable=False, index=True),
        sa.Column("detected_stack", sa.Text(), nullable=False),
        sa.Column("dockerfile", sa.Text(), nullable=False),
        sa.Column("start_command", sa.Text(), nullable=True),
        sa.Column("app_type", sa.Text(), nullable=True),
        sa.Column("app_port", sa.Integer(), nullable=True),
        sa.Column("repo_name", sa.Text(), nullable=True),
        sa.Column("success_count", sa.Integer(), server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("dockerfile_cache")
