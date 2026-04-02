"""WebSocket endpoint for interactive terminal access to running containers."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
import docker

from api.deps import ws_authenticate
from db.database import AsyncSessionLocal
from db.models import Job, JobStatus

router = APIRouter()


@router.websocket("/ws/jobs/{job_id}/terminal")
async def job_terminal_ws(websocket: WebSocket, job_id: str, token: str = Query(default=None)):
    """Interactive terminal to a running container via WebSocket.

    Provides shell access to CLI apps or debugging access to web apps.
    Uses docker exec to create an interactive bash session.
    """
    if not await ws_authenticate(websocket, token):
        return
    await websocket.accept()

    try:
        job_uuid = UUID(job_id)
    except ValueError:
        await websocket.send_json({"type": "error", "data": "Invalid job ID"})
        await websocket.close()
        return

    # Verify job exists and is running
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_uuid)
        if not job:
            await websocket.send_json({"type": "error", "data": "Job not found"})
            await websocket.close()
            return

        if job.status != JobStatus.RUNNING:
            await websocket.send_json({"type": "error", "data": f"Job is not running (status: {job.status.value})"})
            await websocket.close()
            return

        container_id = job.app_container_id
        if not container_id:
            await websocket.send_json({"type": "error", "data": "No running container found"})
            await websocket.close()
            return

    # Create docker exec session
    try:
        client = docker.APIClient(base_url="unix:///var/run/docker.sock")

        # Detect the container's actual working directory; fall back to "/"
        try:
            container_info = client.inspect_container(container_id)
            workdir = container_info.get("Config", {}).get("WorkingDir") or "/"
        except Exception:
            workdir = "/"

        # Try bash first; fall back to sh for minimal images
        shell = "/bin/sh"
        try:
            check = client.exec_create(container_id, cmd=["test", "-f", "/bin/bash"])
            result = client.exec_start(check["Id"])
            info = client.exec_inspect(check["Id"])
            if info.get("ExitCode") == 0:
                shell = "/bin/bash"
        except Exception:
            pass

        exec_id = client.exec_create(
            container_id,
            cmd=shell,
            stdin=True,
            tty=True,
            stdout=True,
            stderr=True,
            workdir=workdir,
        )

        sock = client.exec_start(exec_id["Id"], socket=True, tty=True)
        raw_sock = sock._sock  # Get the underlying socket

        await websocket.send_json({"type": "connected", "data": "Terminal connected. Type commands below.\r\n"})

        # Read from container → send to browser
        async def read_from_container():
            loop = asyncio.get_event_loop()
            while True:
                try:
                    data = await loop.run_in_executor(None, lambda: raw_sock.recv(4096))
                    if not data:
                        break
                    await websocket.send_json({
                        "type": "output",
                        "data": data.decode("utf-8", errors="replace"),
                    })
                except Exception:
                    break

        # Write from browser → send to container
        async def write_to_container():
            while True:
                try:
                    msg = await websocket.receive_json()
                    if msg.get("type") == "input":
                        raw_sock.sendall(msg["data"].encode("utf-8"))
                    elif msg.get("type") == "resize":
                        # Resize terminal
                        client.exec_resize(
                            exec_id["Id"],
                            height=msg.get("rows", 24),
                            width=msg.get("cols", 80),
                        )
                except WebSocketDisconnect:
                    break
                except Exception:
                    break

        # Run both directions concurrently
        read_task = asyncio.create_task(read_from_container())
        write_task = asyncio.create_task(write_to_container())

        done, pending = await asyncio.wait(
            [read_task, write_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "data": str(e)})
        except Exception:
            pass
    finally:
        try:
            raw_sock.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
