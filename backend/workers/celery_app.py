"""Celery application configuration."""

from celery import Celery

from core.config import settings

celery_app = Celery(
    "gitdeploy",
    broker=settings.effective_celery_broker,
    backend=settings.effective_celery_broker,
    include=["workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_acks_on_failure_or_timeout=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=settings.MAX_JOB_TIMEOUT_SECONDS,
    task_time_limit=settings.MAX_JOB_TIMEOUT_SECONDS + 60,
)
