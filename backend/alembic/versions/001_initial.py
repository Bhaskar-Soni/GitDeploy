"""Initial schema - jobs, job_logs, job_databases, install_templates

Revision ID: 001
Revises: None
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enum types
    job_status = postgresql.ENUM(
        "queued", "cloning", "analyzing", "provisioning_db",
        "installing", "success", "failed", "timeout",
        name="job_status", create_type=True,
    )
    install_source = postgresql.ENUM(
        "readme", "config_file", "ai_generated", "template",
        name="install_source", create_type=True,
    )
    log_stream = postgresql.ENUM(
        "stdout", "stderr", "system",
        name="log_stream", create_type=True,
    )
    db_type = postgresql.ENUM(
        "postgresql", "mysql", "mariadb", "mongodb", "redis", "sqlite", "none",
        name="db_type", create_type=True,
    )
    detection_source = postgresql.ENUM(
        "static_scan", "ai_advised", "readme_mentioned",
        name="detection_source", create_type=True,
    )
    job_db_status = postgresql.ENUM(
        "provisioning", "ready", "failed", "torn_down",
        name="job_db_status", create_type=True,
    )

    # Create enum types
    job_status.create(op.get_bind(), checkfirst=True)
    install_source.create(op.get_bind(), checkfirst=True)
    log_stream.create(op.get_bind(), checkfirst=True)
    db_type.create(op.get_bind(), checkfirst=True)
    detection_source.create(op.get_bind(), checkfirst=True)
    job_db_status.create(op.get_bind(), checkfirst=True)

    # Jobs table
    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("repo_url", sa.Text(), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=True),
        sa.Column("repo_owner", sa.Text(), nullable=True),
        sa.Column("status", job_status, nullable=False, server_default="queued"),
        sa.Column("detected_stack", sa.Text(), nullable=True),
        sa.Column("install_source", install_source, nullable=True),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column("commands_run", postgresql.JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # Job logs table
    op.create_table(
        "job_logs",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", sa.UUID(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stream", log_stream, nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_job_logs_job_id", "job_logs", ["job_id"])

    # Job databases table
    op.create_table(
        "job_databases",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", sa.UUID(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("db_type", db_type, nullable=False),
        sa.Column("detection_source", detection_source, nullable=True),
        sa.Column("container_id", sa.Text(), nullable=True),
        sa.Column("container_name", sa.Text(), nullable=True),
        sa.Column("docker_network", sa.Text(), nullable=True),
        sa.Column("db_name", sa.Text(), nullable=True),
        sa.Column("db_host", sa.Text(), nullable=True),
        sa.Column("db_port", sa.Integer(), nullable=True),
        sa.Column("db_user", sa.Text(), nullable=True),
        sa.Column("db_password", sa.Text(), nullable=True),
        sa.Column("env_vars", postgresql.JSONB(), nullable=True),
        sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("torn_down_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", job_db_status, nullable=False, server_default="provisioning"),
    )
    op.create_index("ix_job_databases_job_id", "job_databases", ["job_id"])

    # Install templates table
    op.create_table(
        "install_templates",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("stack", sa.Text(), nullable=False),
        sa.Column("commands", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_install_templates_stack", "install_templates", ["stack"])


def downgrade() -> None:
    op.drop_table("install_templates")
    op.drop_table("job_databases")
    op.drop_table("job_logs")
    op.drop_table("jobs")

    op.execute("DROP TYPE IF EXISTS job_db_status")
    op.execute("DROP TYPE IF EXISTS detection_source")
    op.execute("DROP TYPE IF EXISTS db_type")
    op.execute("DROP TYPE IF EXISTS log_stream")
    op.execute("DROP TYPE IF EXISTS install_source")
    op.execute("DROP TYPE IF EXISTS job_status")
