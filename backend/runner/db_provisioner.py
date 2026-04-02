"""Per-job database container lifecycle manager."""

import time
from datetime import datetime, timezone
from typing import Optional

import docker
from docker.errors import NotFound

from core.config import settings
from runner.credential_manager import CredentialManager, DBCredentials, DBInfo

DB_IMAGES: dict[str, str] = {
    "postgresql": "postgres:16-alpine",
    "mysql": "mysql:8.0",
    "mariadb": "mariadb:11",
    "mongodb": "mongo:7",
    "redis": "redis:7-alpine",
}

DB_PORTS: dict[str, int] = {
    "postgresql": 5432,
    "mysql": 3306,
    "mariadb": 3306,
    "mongodb": 27017,
    "redis": 6379,
}

DB_HEALTH_CMDS: dict[str, callable] = {
    "postgresql": lambda u, p, d: ["pg_isready", "-U", u],
    "mysql": lambda u, p, d: ["mysqladmin", "ping", f"-u{u}", f"-p{p}"],
    "mariadb": lambda u, p, d: ["mariadb-admin", "ping", f"-u{u}", f"-p{p}"],
    "mongodb": lambda u, p, d: ["mongosh", "--eval", "db.runCommand({ping:1})", "-u", u, "-p", p, "--authenticationDatabase", "admin"],
    "redis": lambda u, p, d: ["redis-cli", "-a", p, "ping"],
}


class DBProvisioner:
    """Manages the full lifecycle of per-job database containers."""

    def provision(
        self,
        job_id: str,
        db_type: str,
        credentials: DBCredentials,
        network_name: str,
    ) -> DBInfo:
        """Spin up a database container on the job's isolated network.

        Args:
            job_id: The job UUID string.
            db_type: One of postgresql, mysql, mariadb, mongodb, redis.
            credentials: Generated database credentials.
            network_name: The per-job Docker network name.

        Returns:
            DBInfo with connection details.

        Raises:
            RuntimeError: If the container fails health check.
            ValueError: If db_type is not supported.
        """
        if db_type not in DB_IMAGES:
            raise ValueError(f"Unsupported database type: {db_type}")

        client = docker.from_env()
        container_name = f"gitdeploy_db_{job_id[:8]}_{db_type}"
        container_env = CredentialManager.build_container_env(db_type, credentials)

        # Build container run kwargs
        run_kwargs: dict = {
            "image": DB_IMAGES[db_type],
            "name": container_name,
            "environment": container_env,
            "network": network_name,
            "mem_limit": "256m",
            "detach": True,
            "remove": False,
            "labels": {
                "gitdeploy_job_id": job_id,
                "gitdeploy_db_type": db_type,
            },
        }

        # Redis needs --requirepass passed as command
        if db_type == "redis":
            run_kwargs["command"] = ["redis-server", "--requirepass", credentials.password]

        # Pull image if needed
        try:
            client.images.get(DB_IMAGES[db_type])
        except docker.errors.ImageNotFound:
            client.images.pull(DB_IMAGES[db_type])

        container = client.containers.run(**run_kwargs)

        # Health check: poll every 2s, up to MAX_DB_PROVISION_TIMEOUT_SECONDS
        max_attempts = settings.MAX_DB_PROVISION_TIMEOUT_SECONDS // 2
        health_cmd = DB_HEALTH_CMDS[db_type](
            credentials.user, credentials.password, credentials.db_name
        )

        healthy = False
        for attempt in range(max_attempts):
            time.sleep(2)
            try:
                result = container.exec_run(health_cmd)
                if result.exit_code == 0:
                    healthy = True
                    break
            except Exception:
                # Container might not be ready yet
                pass

            # Check container is still running
            container.reload()
            if container.status != "running":
                logs = container.logs(tail=50).decode("utf-8", errors="replace")
                container.remove(force=True)
                raise RuntimeError(
                    f"{db_type} container exited unexpectedly. Logs:\n{logs}"
                )

        if not healthy:
            logs = container.logs(tail=50).decode("utf-8", errors="replace")
            container.remove(force=True)
            raise RuntimeError(
                f"{db_type} container failed health check after "
                f"{settings.MAX_DB_PROVISION_TIMEOUT_SECONDS}s. Logs:\n{logs}"
            )

        return DBInfo(
            container_id=container.id,
            container_name=container_name,
            host=container_name,  # Resolvable hostname on the shared job network
            port=DB_PORTS[db_type],
            db_name=credentials.db_name,
            user=credentials.user,
            password=credentials.password,
        )

    def teardown(self, job_id: str) -> None:
        """Stop and remove all database containers for a job."""
        client = docker.from_env()

        # Find containers by label
        containers = client.containers.list(
            all=True,
            filters={"label": f"gitdeploy_job_id={job_id}"},
        )

        for container in containers:
            try:
                container.stop(timeout=10)
            except Exception:
                pass
            try:
                container.remove(force=True)
            except Exception:
                pass

    def teardown_container(self, container_id: str) -> None:
        """Stop and remove a specific database container by ID."""
        try:
            client = docker.from_env()
            container = client.containers.get(container_id)
            container.stop(timeout=10)
            container.remove(force=True)
        except NotFound:
            pass
        except Exception:
            try:
                client = docker.from_env()
                client.containers.get(container_id).remove(force=True)
            except Exception:
                pass
