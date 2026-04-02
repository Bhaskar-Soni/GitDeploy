"""WebSocket endpoint for live log streaming."""

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from redis.asyncio import Redis
from sqlalchemy import select

from api.deps import ws_authenticate
from core.config import settings
from db.database import AsyncSessionLocal
from db.models import Job, JobLog, JobStatus

router = APIRouter()

TERMINAL_STATUSES = {JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.TIMEOUT, JobStatus.RUNNING}


@router.websocket("/ws/jobs/{job_id}/logs")
async def job_logs_ws(websocket: WebSocket, job_id: str, token: str = Query(default=None)):
    """Stream job logs via WebSocket.

    1. On connect: replay existing logs from the database.
    2. Subscribe to Redis pub/sub channel for new logs.
    3. On job completion: send final message and close.
    """
    if not await ws_authenticate(websocket, token):
        return
    await websocket.accept()

    try:
        job_uuid = UUID(job_id)
    except ValueError:
        await websocket.send_json({"stream": "system", "message": "Invalid job ID", "final": True})
        await websocket.close()
        return

    # Replay existing logs from database
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_uuid)
        if not job:
            await websocket.send_json({"stream": "system", "message": "Job not found", "final": True})
            await websocket.close()
            return

        stmt = (
            select(JobLog)
            .where(JobLog.job_id == job_uuid)
            .order_by(JobLog.timestamp.asc())
        )
        result = await session.execute(stmt)
        existing_logs = result.scalars().all()

        for log in existing_logs:
            await websocket.send_json({
                "stream": log.stream.value,
                "message": log.message,
                "timestamp": log.timestamp.isoformat(),
            })

        # If job already finished or running, notify and close
        if job.status in TERMINAL_STATUSES:
            if job.status == JobStatus.RUNNING:
                await websocket.send_json({
                    "stream": "system",
                    "message": f"App is running" + (f" at {job.proxy_url}" if job.proxy_url else ""),
                    "running": True,
                    "proxy_url": job.proxy_url,
                })
            else:
                await websocket.send_json({
                    "stream": "system",
                    "message": f"Job finished with status: {job.status.value}",
                    "final": True,
                })
            await websocket.close()
            return

    # Subscribe to Redis channel for live updates
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = redis.pubsub()
    channel = f"job_logs:{job_id}"
    await pubsub.subscribe(channel)

    try:
        while True:
            message = await asyncio.wait_for(
                pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                timeout=5.0,
            )

            if message and message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                await websocket.send_json(data)

                # Check if this is the final message
                if data.get("final") or data.get("running"):
                    break

    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        # Check if job has finished or started running (in case we missed the message)
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_uuid)
            if job and job.status in TERMINAL_STATUSES:
                try:
                    if job.status == JobStatus.RUNNING:
                        await websocket.send_json({
                            "stream": "system",
                            "message": f"App is running" + (f" at {job.proxy_url}" if job.proxy_url else ""),
                            "running": True,
                            "proxy_url": job.proxy_url,
                        })
                    else:
                        await websocket.send_json({
                            "stream": "system",
                            "message": f"Job finished with status: {job.status.value}",
                            "final": True,
                        })
                except Exception:
                    pass
    except Exception:
        pass
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await redis.close()
        try:
            await websocket.close()
        except Exception:
            pass
