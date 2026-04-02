"""SQLAlchemy models for GitDeploy."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from db.database import Base


class AppType(str, enum.Enum):
    WEB = "web"
    CLI = "cli"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    CLONING = "cloning"
    ANALYZING = "analyzing"
    PROVISIONING_DB = "provisioning_db"
    INSTALLING = "installing"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


class InstallSource(str, enum.Enum):
    README = "readme"
    CONFIG_FILE = "config_file"
    AI_GENERATED = "ai_generated"
    TEMPLATE = "template"


class LogStream(str, enum.Enum):
    STDOUT = "stdout"
    STDERR = "stderr"
    SYSTEM = "system"


class DBType(str, enum.Enum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    MARIADB = "mariadb"
    MONGODB = "mongodb"
    REDIS = "redis"
    SQLITE = "sqlite"
    NONE = "none"


class DetectionSource(str, enum.Enum):
    STATIC_SCAN = "static_scan"
    AI_ADVISED = "ai_advised"
    README_MENTIONED = "readme_mentioned"


class JobDBStatus(str, enum.Enum):
    PROVISIONING = "provisioning"
    READY = "ready"
    FAILED = "failed"
    TORN_DOWN = "torn_down"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_url = Column(Text, nullable=False)
    repo_name = Column(Text, nullable=True)
    repo_owner = Column(Text, nullable=True)
    status = Column(
        Enum(JobStatus, name="job_status", create_constraint=True),
        default=JobStatus.QUEUED,
        nullable=False,
    )
    detected_stack = Column(Text, nullable=True)
    install_source = Column(
        Enum(InstallSource, name="install_source", create_constraint=True),
        nullable=True,
    )
    ai_confidence = Column(Float, nullable=True)
    commands_run = Column(JSONB, nullable=True, default=list)
    error_message = Column(Text, nullable=True)
    app_type = Column(
        Enum(AppType, name="app_type", create_constraint=True),
        nullable=True,
    )
    app_port = Column(Integer, nullable=True)
    proxy_port = Column(Integer, nullable=True)  # Host port mapped to container
    proxy_url = Column(Text, nullable=True)  # URL the user can access
    start_command = Column(Text, nullable=True)
    app_container_id = Column(Text, nullable=True)  # Running app container ID
    docker_image = Column(Text, nullable=True)  # Docker image used (for cleanup)
    clone_path = Column(Text, nullable=True)  # Path to cloned repo on disk
    usage_instructions = Column(Text, nullable=True)  # AI-generated usage guide
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # Auto-cleanup time

    logs = relationship("JobLog", back_populates="job", cascade="all, delete-orphan")
    databases = relationship("JobDatabase", back_populates="job", cascade="all, delete-orphan")


class JobLog(Base):
    __tablename__ = "job_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stream = Column(
        Enum(LogStream, name="log_stream", create_constraint=True),
        nullable=False,
    )
    message = Column(Text, nullable=False)
    timestamp = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    job = relationship("Job", back_populates="logs")


class JobDatabase(Base):
    __tablename__ = "job_databases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    db_type = Column(
        Enum(DBType, name="db_type", create_constraint=True),
        nullable=False,
    )
    detection_source = Column(
        Enum(DetectionSource, name="detection_source", create_constraint=True),
        nullable=True,
    )
    container_id = Column(Text, nullable=True)
    container_name = Column(Text, nullable=True)
    docker_network = Column(Text, nullable=True)
    db_name = Column(Text, nullable=True)
    db_host = Column(Text, nullable=True)
    db_port = Column(Integer, nullable=True)
    db_user = Column(Text, nullable=True)
    db_password = Column(Text, nullable=True)  # Stored encrypted via Fernet
    env_vars = Column(JSONB, nullable=True)
    provisioned_at = Column(DateTime(timezone=True), nullable=True)
    torn_down_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        Enum(JobDBStatus, name="job_db_status", create_constraint=True),
        default=JobDBStatus.PROVISIONING,
        nullable=False,
    )

    job = relationship("Job", back_populates="databases")


class InstallTemplate(Base):
    __tablename__ = "install_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stack = Column(Text, nullable=False, index=True)
    commands = Column(JSONB, nullable=False)
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class DockerfileCache(Base):
    """Stores successful Dockerfiles so similar repos don't need AI calls."""
    __tablename__ = "dockerfile_cache"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stack_signature = Column(Text, nullable=False, index=True)  # hash of key file names
    detected_stack = Column(Text, nullable=False)
    dockerfile = Column(Text, nullable=False)
    start_command = Column(Text, nullable=True)
    app_type = Column(Text, nullable=True)
    app_port = Column(Integer, nullable=True)
    repo_name = Column(Text, nullable=True)  # which repo this came from
    success_count = Column(Integer, default=1)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(Text, unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    is_active = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
