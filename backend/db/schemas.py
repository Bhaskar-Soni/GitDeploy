"""Pydantic schemas for API request/response validation."""

import re
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class JobCreate(BaseModel):
    repo_url: str

    @field_validator("repo_url")
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        pattern = r"^https?://github\.com/[\w\-\.]+/[\w\-\.]+/?$"
        if not re.match(pattern, v.strip()):
            raise ValueError("Must be a valid public GitHub repository URL")
        return v.strip().rstrip("/")


class LogEntry(BaseModel):
    stream: str
    message: str
    timestamp: datetime

    model_config = {"from_attributes": True}


class JobDatabaseResponse(BaseModel):
    id: UUID
    db_type: str
    detection_source: Optional[str] = None
    container_name: Optional[str] = None
    docker_network: Optional[str] = None
    db_name: Optional[str] = None
    db_host: Optional[str] = None
    db_port: Optional[int] = None
    db_user: Optional[str] = None
    env_vars: Optional[dict[str, str]] = None
    status: str
    provisioned_at: Optional[datetime] = None
    torn_down_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class JobResponse(BaseModel):
    job_id: UUID = Field(alias="id")
    repo_url: str
    repo_name: Optional[str] = None
    repo_owner: Optional[str] = None
    status: str
    detected_stack: Optional[str] = None
    install_source: Optional[str] = None
    ai_confidence: Optional[float] = None
    commands_run: Optional[list[str]] = None
    error_message: Optional[str] = None
    app_type: Optional[str] = None
    app_port: Optional[int] = None
    proxy_url: Optional[str] = None
    start_command: Optional[str] = None
    usage_instructions: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
    docker_image: Optional[str] = None
    app_container_id: Optional[str] = None
    databases: list[JobDatabaseResponse] = []

    model_config = {"from_attributes": True, "populate_by_name": True}


class JobCreateResponse(BaseModel):
    job_id: UUID
    status: str
    repo_url: str
    created_at: datetime


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
    page: int
    per_page: int
    total_pages: int


class ErrorResponse(BaseModel):
    error: str
    code: str
