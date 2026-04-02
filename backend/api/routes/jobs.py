"""REST endpoints for job creation, listing, and management."""

import math
import re
import shutil
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.database import get_db
from db.models import Job, JobDatabase, JobLog, JobStatus
from db.schemas import (
    ErrorResponse,
    JobCreate,
    JobCreateResponse,
    JobDatabaseResponse,
    JobListResponse,
    JobResponse,
    LogEntry,
)
from runner.credential_manager import CredentialManager

router = APIRouter(tags=["jobs"])


def _parse_github_url(url: str) -> tuple[str, str]:
    """Extract owner and repo name from a GitHub URL."""
    match = re.match(r"https?://github\.com/([\w\-\.]+)/([\w\-\.]+)", url)
    if match:
        return match.group(1), match.group(2)
    return "", ""


def _mask_db_response(db: JobDatabase) -> JobDatabaseResponse:
    """Convert a JobDatabase model to a response with masked passwords."""
    env_vars = db.env_vars or {}
    masked = CredentialManager.mask_env_vars(env_vars) if env_vars else {}

    return JobDatabaseResponse(
        id=db.id,
        db_type=db.db_type.value if db.db_type else "none",
        detection_source=db.detection_source.value if db.detection_source else None,
        container_name=db.container_name,
        docker_network=db.docker_network,
        db_name=db.db_name,
        db_host=db.db_host,
        db_port=db.db_port,
        db_user=db.db_user,
        env_vars=masked,
        status=db.status.value if db.status else "unknown",
        provisioned_at=db.provisioned_at,
        torn_down_at=db.torn_down_at,
    )


@router.post("/jobs", response_model=JobCreateResponse, status_code=201)
async def create_job(payload: JobCreate, db: AsyncSession = Depends(get_db)):
    """Create a new deploy job and enqueue it for processing."""
    owner, repo_name = _parse_github_url(payload.repo_url)

    job = Job(
        repo_url=payload.repo_url,
        repo_name=repo_name,
        repo_owner=owner,
        status=JobStatus.QUEUED,
    )
    db.add(job)
    await db.flush()
    await db.commit()  # commit before queuing so worker can find the job

    # Import here to avoid circular dependency with celery
    from workers.tasks import process_repo_job

    process_repo_job.delay(str(job.id))

    return JobCreateResponse(
        job_id=job.id,
        status=job.status.value,
        repo_url=job.repo_url,
        created_at=job.created_at,
    )


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all jobs, paginated, newest first."""
    # Count
    count_stmt = select(func.count(Job.id))
    total = (await db.execute(count_stmt)).scalar_one()
    total_pages = max(1, math.ceil(total / per_page))

    # Fetch page
    stmt = (
        select(Job)
        .options(selectinload(Job.databases))
        .order_by(Job.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await db.execute(stmt)
    jobs = result.scalars().all()

    job_responses = []
    for job in jobs:
        databases = [_mask_db_response(d) for d in job.databases]
        job_responses.append(
            JobResponse(
                id=job.id,
                repo_url=job.repo_url,
                repo_name=job.repo_name,
                repo_owner=job.repo_owner,
                status=job.status.value,
                detected_stack=job.detected_stack,
                install_source=job.install_source.value if job.install_source else None,
                ai_confidence=job.ai_confidence,
                commands_run=job.commands_run,
                error_message=job.error_message,
                started_at=job.started_at,
                finished_at=job.finished_at,
                created_at=job.created_at,
                databases=databases,
            )
        )

    return JobListResponse(
        jobs=job_responses,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get detailed job information including database provisioning info."""
    stmt = (
        select(Job)
        .options(selectinload(Job.databases))
        .where(Job.id == job_id)
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail={"error": "Job not found", "code": "JOB_NOT_FOUND"})

    databases = [_mask_db_response(d) for d in job.databases]

    return JobResponse(
        id=job.id,
        repo_url=job.repo_url,
        repo_name=job.repo_name,
        repo_owner=job.repo_owner,
        status=job.status.value,
        detected_stack=job.detected_stack,
        install_source=job.install_source.value if job.install_source else None,
        ai_confidence=job.ai_confidence,
        commands_run=job.commands_run,
        error_message=job.error_message,
        app_type=job.app_type.value if job.app_type else None,
        app_port=job.app_port,
        proxy_url=job.proxy_url,
        start_command=job.start_command,
        usage_instructions=job.usage_instructions,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        expires_at=job.expires_at,
        databases=databases,
    )


@router.get("/jobs/{job_id}/logs", response_model=list[LogEntry])
async def get_job_logs(job_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get all stored logs for a job ordered by timestamp."""
    # Verify job exists
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error": "Job not found", "code": "JOB_NOT_FOUND"})

    stmt = (
        select(JobLog)
        .where(JobLog.job_id == job_id)
        .order_by(JobLog.timestamp.asc())
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    return [
        LogEntry(
            stream=log.stream.value,
            message=log.message,
            timestamp=log.timestamp,
        )
        for log in logs
    ]


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: UUID, db: AsyncSession = Depends(get_db)):
    """Cancel a queued or running job."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error": "Job not found", "code": "JOB_NOT_FOUND"})

    if job.status in (JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.TIMEOUT):
        raise HTTPException(
            status_code=400,
            detail={"error": "Job already completed", "code": "JOB_COMPLETED"},
        )

    # Try to revoke the Celery task
    try:
        from workers.celery_app import celery_app
        celery_app.control.revoke(str(job_id), terminate=True, signal="SIGTERM")
    except Exception:
        pass

    # Try to kill any running containers
    try:
        from runner.docker_runner import DockerRunner
        DockerRunner().kill_container(str(job_id))
    except Exception:
        pass

    # Try to tear down DB containers
    try:
        from runner.db_provisioner import DBProvisioner
        DBProvisioner().teardown(str(job_id))
    except Exception:
        pass

    job.status = JobStatus.FAILED
    job.error_message = "Cancelled by user"
    job.finished_at = datetime.now(timezone.utc)
    await db.flush()
    await db.commit()

    return {"message": "Job cancelled", "job_id": str(job_id)}


@router.post("/jobs/{job_id}/restart")
async def restart_job(job_id: UUID, db: AsyncSession = Depends(get_db)):
    """Restart a stopped job. Reuses existing container if available, otherwise re-deploys."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error": "Job not found", "code": "JOB_NOT_FOUND"})

    if job.status not in (JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.TIMEOUT):
        raise HTTPException(status_code=400, detail={"error": "Job is not stopped", "code": "JOB_NOT_STOPPED"})

    # Reset to queued state so the frontend shows progress
    job.status = JobStatus.QUEUED
    job.finished_at = None
    job.error_message = None
    await db.flush()
    await db.commit()

    from workers.tasks import restart_job_task
    restart_job_task.delay(str(job_id))

    return {"message": "Restart initiated", "job_id": str(job_id)}


@router.delete("/jobs/{job_id}/purge")
async def purge_job(job_id: UUID, db: AsyncSession = Depends(get_db)):
    """Permanently delete a job and clean up ALL resources (containers, images, cloned files)."""
    stmt = (
        select(Job)
        .options(selectinload(Job.databases))
        .where(Job.id == job_id)
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail={"error": "Job not found", "code": "JOB_NOT_FOUND"})

    job_id_str = str(job_id)

    # Revoke Celery task if still queued/running
    try:
        from workers.celery_app import celery_app
        celery_app.control.revoke(job_id_str, terminate=True, signal="SIGTERM")
    except Exception:
        pass

    # Kill all Docker resources (containers, compose project, images)
    try:
        from runner.docker_runner import DockerRunner
        DockerRunner().purge_job(job_id_str, docker_image=job.docker_image)
    except Exception:
        pass

    # Tear down DB containers and networks
    try:
        from runner.db_provisioner import DBProvisioner
        DBProvisioner().teardown(job_id_str)
    except Exception:
        pass

    # Remove leftover Docker networks
    try:
        from runner.network_manager import NetworkManager
        for db_record in job.databases:
            if db_record.docker_network:
                try:
                    NetworkManager.remove(db_record.docker_network)
                except Exception:
                    pass
    except Exception:
        pass

    # Remove cloned repo files
    if job.clone_path and os.path.isdir(job.clone_path):
        try:
            shutil.rmtree(job.clone_path, ignore_errors=True)
        except Exception:
            pass

    # Delete job record (cascades to logs and databases)
    await db.delete(job)
    await db.flush()
    await db.commit()

    return {"message": "Job purged", "job_id": job_id_str}


@router.post("/jobs/{job_id}/stop")
async def stop_job(job_id: UUID, db: AsyncSession = Depends(get_db)):
    """Stop a running job and clean up all resources."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error": "Job not found", "code": "JOB_NOT_FOUND"})

    if job.status != JobStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail={"error": "Job is not running", "code": "JOB_NOT_RUNNING"},
        )

    from workers.tasks import stop_job as stop_job_task
    stop_job_task.delay(str(job_id))

    return {"message": "Stop initiated", "job_id": str(job_id)}
