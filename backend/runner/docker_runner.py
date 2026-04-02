"""Docker sandbox execution layer for running repository install commands and serving apps."""

import os
import subprocess
import threading
import time
from typing import Callable, Optional

import docker
import yaml
from docker.errors import ImageNotFound, APIError

from core.config import settings


class DockerRunner:
    """Executes install commands and runs apps inside isolated Docker containers."""

    # Preferred custom sandbox images (pre-built with extra tooling)
    STACK_TO_IMAGE: dict[str, str] = {
        "node": "gitdeploy-node:latest",
        "python-pip": "gitdeploy-python:latest",
        "python-poetry": "gitdeploy-python:latest",
        "python-conda": "gitdeploy-python:latest",
        "rust": "gitdeploy-rust:latest",
        "go": "gitdeploy-go:latest",
        "java-maven": "gitdeploy-generic:latest",
        "java-gradle": "gitdeploy-generic:latest",
        "ruby": "gitdeploy-generic:latest",
        "php": "gitdeploy-generic:latest",
        "dotnet": "gitdeploy-generic:latest",
        "elixir": "gitdeploy-generic:latest",
        "generic": "gitdeploy-generic:latest",
    }

    # Official Docker Hub fallbacks when custom images aren't built
    STACK_TO_FALLBACK_IMAGE: dict[str, str] = {
        "node": "node:20-slim",
        "python-pip": "python:3.12-slim",
        "python-poetry": "python:3.12-slim",
        "python-conda": "python:3.12-slim",
        "rust": "rust:1.77-slim",
        "go": "golang:1.22-bookworm",
        "java-maven": "maven:3.9-eclipse-temurin-21",
        "java-gradle": "gradle:8-jdk21",
        "ruby": "ruby:3.3-slim",
        "php": "php:8.3-cli",
        "dotnet": "mcr.microsoft.com/dotnet/sdk:8.0",
        "elixir": "elixir:1.16-slim",
        "generic": "ubuntu:24.04",
    }

    # Infrastructure service names and images to skip when detecting main web service
    _INFRA_SERVICE_NAMES = {"db", "database", "postgres", "postgresql", "mysql", "mariadb",
                             "redis", "mongo", "mongodb", "cache", "queue", "rabbitmq",
                             "kafka", "elasticsearch", "minio", "zookeeper"}
    _INFRA_IMAGES = {"postgres", "mysql", "mariadb", "mongo", "redis", "rabbitmq",
                      "kafka", "elasticsearch", "minio", "zookeeper", "influxdb"}
    # DB ports to skip when detecting web app ports
    _DB_PORTS = {5432, 3306, 27017, 6379, 5672, 9092, 9200, 2181, 9000}
    # Common web app ports (higher priority)
    _WEB_PORTS = {80, 443, 3000, 3001, 4000, 4200, 5000, 5173, 8000, 8080, 8081, 8888, 9000}

    def _get_volume_info(self, repo_path: str) -> tuple[str, str]:
        """Get volume name and working directory for mounting repo."""
        volume_base = settings.GITHUB_CLONE_DIR.rsplit("/repos", 1)[0]
        repo_subpath = os.path.relpath(repo_path, volume_base)
        volume_name = "gitdeploy_gitdeploy_repos"
        working_dir = f"{volume_base}/{repo_subpath}"
        return volume_name, working_dir

    def _get_image(self, stack: str) -> str:
        """Get sandbox image for a stack, falling back to official Docker Hub images."""
        client = docker.from_env()
        preferred = self.STACK_TO_IMAGE.get(stack, "gitdeploy-generic:latest")
        try:
            client.images.get(preferred)
            return preferred
        except ImageNotFound:
            fallback = self.STACK_TO_FALLBACK_IMAGE.get(stack, "ubuntu:24.04")
            return fallback

    # ── Compose deployment ─────────────────────────────────────────────────────

    def _parse_compose_info(self, repo_path: str) -> tuple[int, str, str]:
        """Parse docker-compose.yml to find main web service name and internal port.

        Returns (internal_port, service_name, compose_filename).
        """
        compose_file = None
        for name in ["docker-compose.yml", "docker-compose.yaml"]:
            if os.path.isfile(os.path.join(repo_path, name)):
                compose_file = name
                break

        if not compose_file:
            return 3000, "app", "docker-compose.yml"

        try:
            with open(os.path.join(repo_path, compose_file)) as f:
                compose = yaml.safe_load(f)
        except Exception:
            return 3000, "app", compose_file

        services = compose.get("services", {})
        if not services:
            return 3000, "app", compose_file

        web_candidates: list[tuple[str, int]] = []

        for svc_name, svc_def in services.items():
            if not isinstance(svc_def, dict):
                continue
            if svc_name.lower() in self._INFRA_SERVICE_NAMES:
                continue
            image = svc_def.get("image", "") or ""
            if any(infra in image.lower() for infra in self._INFRA_IMAGES):
                continue

            # Collect ports from "ports" or "expose" keys
            raw_ports = svc_def.get("ports", []) or svc_def.get("expose", [])
            for port_def in raw_ports:
                port_str = str(port_def)
                # Handle "host:container/proto" or "container" or "host:container"
                container_part = port_str.split(":")[-1].split("/")[0]
                try:
                    container_port = int(container_part)
                except ValueError:
                    continue
                if container_port not in self._DB_PORTS:
                    web_candidates.append((svc_name, container_port))
                    break

        if not web_candidates:
            # No explicit ports — use the first non-infra service with port 3000
            for svc_name in services:
                if svc_name.lower() not in self._INFRA_SERVICE_NAMES:
                    return 3000, svc_name, compose_file
            return 3000, next(iter(services)), compose_file

        # Prefer candidates with known web ports
        for svc_name, port in web_candidates:
            if port in self._WEB_PORTS:
                return port, svc_name, compose_file

        return web_candidates[0][1], web_candidates[0][0], compose_file

    def run_compose(
        self,
        repo_path: str,
        job_id: str,
        proxy_port: int,
        log_callback: Callable[[str, str, str], None],
    ) -> tuple[str, int]:
        """Deploy a repo using its docker-compose.yml.

        Overrides the main web service's port mapping to use proxy_port so we control routing.
        Returns (main_container_id, internal_app_port).
        """
        internal_port, main_service, compose_file = self._parse_compose_info(repo_path)
        project_name = f"gitdeploy{job_id[:8]}"

        log_callback(job_id, f"Detected main service: '{main_service}' on internal port {internal_port}", "stdout")
        log_callback(job_id, f"Mapping to proxy port {proxy_port}...", "stdout")

        # Write a compose override to remap only the main service's port
        override = {
            "services": {
                main_service: {
                    "ports": [f"{proxy_port}:{internal_port}"]
                }
            }
        }
        override_path = os.path.join(repo_path, "docker-compose.gitdeploy-override.yml")
        try:
            with open(override_path, "w") as f:
                yaml.dump(override, f)

            cmd = [
                "docker-compose",
                "-f", compose_file,
                "-f", "docker-compose.gitdeploy-override.yml",
                "-p", project_name,
                "up", "--build", "-d",
            ]

            proc = subprocess.Popen(
                cmd,
                cwd=repo_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in iter(proc.stdout.readline, ""):
                line = line.rstrip()
                if line:
                    log_callback(job_id, line, "stdout")

            proc.wait(timeout=600)
            if proc.returncode != 0:
                raise RuntimeError(f"docker-compose up failed (exit {proc.returncode})")
        finally:
            try:
                os.remove(override_path)
            except Exception:
                pass

        # Find main service container and label it for lifecycle tracking
        client = docker.from_env()
        main_container_id = ""
        containers = client.containers.list(
            filters={"label": f"com.docker.compose.project={project_name}"}
        )
        for c in containers:
            if c.labels.get("com.docker.compose.service") == main_service:
                main_container_id = c.id
                break
        if not main_container_id and containers:
            main_container_id = containers[0].id

        # Tag all compose containers with our job id for cleanup
        for c in containers:
            try:
                c.reload()
                # Docker SDK doesn't support updating labels post-creation;
                # we track by compose project name instead
            except Exception:
                pass

        # Stream main service logs in background
        if main_container_id:
            def _stream():
                try:
                    container = client.containers.get(main_container_id)
                    for chunk in container.logs(stream=True, follow=True):
                        line = chunk.decode("utf-8", errors="replace").rstrip()
                        if line:
                            stream = "stderr" if any(
                                kw in line.lower() for kw in ["error", "traceback", "exception", "fatal"]
                            ) else "stdout"
                            log_callback(job_id, line, stream)
                except Exception:
                    pass

            threading.Thread(target=_stream, daemon=True).start()

        return main_container_id, internal_port

    def kill_compose(self, job_id: str) -> None:
        """Stop and remove all containers in a compose-deployed project."""
        project_name = f"gitdeploy{job_id[:8]}"
        try:
            subprocess.run(
                ["docker-compose", "-p", project_name, "down", "--volumes", "--remove-orphans"],
                timeout=60,
                capture_output=True,
            )
        except Exception:
            pass
        # Fallback: remove by compose project label
        try:
            client = docker.from_env()
            for c in client.containers.list(
                all=True,
                filters={"label": f"com.docker.compose.project={project_name}"},
            ):
                try:
                    c.remove(force=True)
                except Exception:
                    pass
        except Exception:
            pass

    # ── Docker Hub image deployment (fastest path) ─────────────────────────────

    def run_image(
        self,
        image: str,
        job_id: str,
        proxy_port: int,
        app_port: int,
        log_callback: Callable[[str, str, str], None],
        env_vars: Optional[dict[str, str]] = None,
        network_name: Optional[str] = None,
    ) -> str:
        """Pull a pre-built Docker Hub image and run it. Returns container_id."""
        client = docker.from_env()
        container_name = f"gitdeploy_app_{job_id[:8]}"

        log_callback(job_id, f"Pulling image {image}...", "stdout")
        try:
            for line in client.api.pull(image, stream=True, decode=True):
                status = line.get("status", "")
                progress = line.get("progress", "")
                if status and "Pull complete" in status or "Already exists" in status:
                    log_callback(job_id, f"  {status}", "stdout")
        except Exception as e:
            raise RuntimeError(f"Failed to pull image '{image}': {e}")

        try:
            client.containers.get(container_name).remove(force=True)
        except Exception:
            pass

        run_kwargs: dict = {
            "image": image,
            "name": container_name,
            "environment": env_vars or {},
            "mem_limit": "1g",
            "nano_cpus": 2_000_000_000,
            "read_only": False,
            "security_opt": ["no-new-privileges"],
            "remove": False,
            "detach": True,
            "ports": {f"{app_port}/tcp": proxy_port},
            "labels": {
                "gitdeploy_job_id": job_id,
                "gitdeploy_role": "app",
                "gitdeploy_app_port": str(app_port),
                "gitdeploy_proxy_port": str(proxy_port),
            },
        }

        try:
            container = client.containers.run(**run_kwargs)
        except APIError as e:
            raise RuntimeError(f"Failed to start container: {e}")

        if network_name:
            try:
                client.networks.get(network_name).connect(container)
            except Exception:
                pass

        def _stream():
            try:
                for chunk in container.logs(stream=True, follow=True):
                    line = chunk.decode("utf-8", errors="replace").rstrip()
                    if line:
                        stream = "stderr" if any(
                            kw in line.lower() for kw in ["error", "traceback", "exception", "fatal"]
                        ) else "stdout"
                        log_callback(job_id, line, stream)
            except Exception:
                pass

        threading.Thread(target=_stream, daemon=True).start()
        return container.id

    # ── Dockerfile deployment ──────────────────────────────────────────────────

    def run_dockerfile(
        self,
        repo_path: str,
        job_id: str,
        proxy_port: int,
        app_port: int,
        log_callback: Callable[[str, str, str], None],
        env_vars: Optional[dict[str, str]] = None,
        network_name: Optional[str] = None,
    ) -> str:
        """Build repo's Dockerfile and run the resulting image. Returns container_id."""
        client = docker.from_env()
        image_tag = f"gitdeploy_img_{job_id[:8]}:latest"
        container_name = f"gitdeploy_app_{job_id[:8]}"

        log_callback(job_id, "Building Docker image from repo Dockerfile...", "stdout")
        try:
            _, build_logs = client.images.build(
                path=repo_path,
                tag=image_tag,
                rm=True,
                forcerm=True,
                pull=True,
            )
            for entry in build_logs:
                line = (entry.get("stream") or entry.get("status") or "").rstrip()
                if line:
                    log_callback(job_id, line, "stdout")
        except Exception as e:
            raise RuntimeError(f"Docker build failed: {e}")

        # Remove any existing container with same name
        try:
            client.containers.get(container_name).remove(force=True)
        except Exception:
            pass

        run_kwargs: dict = {
            "image": image_tag,
            "name": container_name,
            "environment": env_vars or {},
            "mem_limit": "1g",
            "nano_cpus": 2_000_000_000,
            "read_only": False,
            "security_opt": ["no-new-privileges"],
            "remove": False,
            "detach": True,
            "ports": {f"{app_port}/tcp": proxy_port},
            "labels": {
                "gitdeploy_job_id": job_id,
                "gitdeploy_role": "app",
                "gitdeploy_app_port": str(app_port),
                "gitdeploy_proxy_port": str(proxy_port),
            },
        }

        try:
            container = client.containers.run(**run_kwargs)
        except APIError as e:
            raise RuntimeError(f"Failed to start container: {e}")

        if network_name:
            try:
                client.networks.get(network_name).connect(container)
            except Exception:
                pass

        def _stream():
            try:
                for chunk in container.logs(stream=True, follow=True):
                    line = chunk.decode("utf-8", errors="replace").rstrip()
                    if line:
                        stream = "stderr" if any(
                            kw in line.lower() for kw in ["error", "traceback", "exception", "fatal"]
                        ) else "stdout"
                        log_callback(job_id, line, stream)
            except Exception:
                pass

        threading.Thread(target=_stream, daemon=True).start()
        return container.id

    # ── Sandbox deployment (package-manager based) ─────────────────────────────

    def execute(
        self,
        repo_path: str,
        commands: list[str],
        job_id: str,
        stack: str,
        log_callback: Callable[[str, str, str], None],
        env_vars: Optional[dict[str, str]] = None,
        network_name: Optional[str] = None,
    ) -> None:
        """Run install commands inside an isolated Docker container (exits when done)."""
        client = docker.from_env()
        image = self._get_image(stack)
        volume_name, working_dir = self._get_volume_info(repo_path)

        script = "set -e\n" + "\n".join(commands)
        container_name = f"gitdeploy_app_{job_id[:8]}"

        run_kwargs: dict = {
            "image": image,
            "command": ["bash", "-c", script],
            "name": container_name,
            "volumes": {volume_name: {"bind": settings.GITHUB_CLONE_DIR.rsplit("/repos", 1)[0], "mode": "rw"}},
            "working_dir": working_dir,
            "environment": env_vars or {},
            "mem_limit": "512m",
            "nano_cpus": 1_000_000_000,
            "read_only": False,
            "security_opt": ["no-new-privileges"],
            "remove": False,
            "detach": True,
            "stdout": True,
            "stderr": True,
            "labels": {
                "gitdeploy_job_id": job_id,
                "gitdeploy_role": "app",
            },
        }

        if network_name:
            run_kwargs["network"] = network_name
        else:
            run_kwargs["network_mode"] = "none"

        try:
            container = client.containers.run(**run_kwargs)
        except APIError as e:
            raise RuntimeError(f"Failed to start container: {e}")

        try:
            for log_chunk in container.logs(stream=True, follow=True):
                line = log_chunk.decode("utf-8", errors="replace").rstrip()
                if line:
                    log_callback(job_id, line, "stdout")

            result = container.wait(timeout=settings.MAX_JOB_TIMEOUT_SECONDS)
            exit_code = result.get("StatusCode", -1)

            if exit_code != 0:
                stderr_logs = container.logs(stdout=False, stderr=True, tail=50)
                stderr_text = stderr_logs.decode("utf-8", errors="replace").strip()
                if stderr_text:
                    for line in stderr_text.splitlines():
                        log_callback(job_id, line, "stderr")
                raise RuntimeError(f"Container exited with code {exit_code}")
        finally:
            try:
                container.remove(force=True)
            except Exception:
                pass

    def run_app(
        self,
        repo_path: str,
        install_commands: list[str],
        start_command: str,
        job_id: str,
        stack: str,
        app_port: int,
        proxy_port: int,
        log_callback: Callable[[str, str, str], None],
        env_vars: Optional[dict[str, str]] = None,
        network_name: Optional[str] = None,
    ) -> str:
        """Install dependencies and run the app, keeping the container alive.

        Returns the container ID.
        """
        client = docker.from_env()
        image = self._get_image(stack)
        volume_name, working_dir = self._get_volume_info(repo_path)

        install_script = " && ".join(install_commands) if install_commands else "true"
        full_script = f"set -e\n{install_script}\necho '--- GITDEPLOY: Install complete, starting app ---'\nexec {start_command}"

        container_name = f"gitdeploy_app_{job_id[:8]}"
        try:
            client.containers.get(container_name).remove(force=True)
        except Exception:
            pass

        run_kwargs: dict = {
            "image": image,
            "command": ["bash", "-c", full_script],
            "name": container_name,
            "volumes": {volume_name: {"bind": settings.GITHUB_CLONE_DIR.rsplit("/repos", 1)[0], "mode": "rw"}},
            "working_dir": working_dir,
            "environment": env_vars or {},
            "mem_limit": "1g",
            "nano_cpus": 2_000_000_000,
            "read_only": False,
            "security_opt": ["no-new-privileges"],
            "remove": False,
            "detach": True,
            "stdout": True,
            "stderr": True,
            "ports": {f"{app_port}/tcp": proxy_port},
            "labels": {
                "gitdeploy_job_id": job_id,
                "gitdeploy_role": "app",
                "gitdeploy_app_port": str(app_port),
                "gitdeploy_proxy_port": str(proxy_port),
            },
        }

        try:
            container = client.containers.run(**run_kwargs)
        except APIError as e:
            raise RuntimeError(f"Failed to start app container: {e}")

        if network_name:
            try:
                client.networks.get(network_name).connect(container)
            except Exception:
                pass

        def stream_logs():
            try:
                for log_chunk in container.logs(stream=True, follow=True):
                    line = log_chunk.decode("utf-8", errors="replace").rstrip()
                    if line:
                        stream = "stderr" if any(
                            kw in line.lower() for kw in ["error", "traceback", "exception", "fatal"]
                        ) else "stdout"
                        log_callback(job_id, line, stream)
            except Exception:
                pass

        threading.Thread(target=stream_logs, daemon=True).start()
        return container.id

    # ── Lifecycle management ───────────────────────────────────────────────────

    def stop_container(self, job_id: str) -> None:
        """Stop (but preserve) the app container for a job so it can be restarted."""
        try:
            client = docker.from_env()
            for container in client.containers.list(
                all=True,
                filters={"label": f"gitdeploy_job_id={job_id}"},
            ):
                if container.labels.get("gitdeploy_role") == "app":
                    try:
                        container.stop(timeout=10)
                    except Exception:
                        pass
        except Exception:
            pass

    def start_container(self, container_id: str) -> bool:
        """Start a stopped container. Returns True if successfully started."""
        try:
            client = docker.from_env()
            container = client.containers.get(container_id)
            container.start()
            return True
        except Exception:
            return False

    def kill_container(self, job_id: str) -> None:
        """Force-kill and remove any app container for a job."""
        try:
            client = docker.from_env()
            for container in client.containers.list(
                all=True,
                filters={"label": f"gitdeploy_job_id={job_id}"},
            ):
                if container.labels.get("gitdeploy_role") == "app":
                    try:
                        container.kill()
                    except Exception:
                        pass
                    container.remove(force=True)
        except Exception:
            pass

    def is_container_running(self, container_id: str) -> bool:
        """Check if a container is still running."""
        try:
            client = docker.from_env()
            return client.containers.get(container_id).status == "running"
        except Exception:
            return False

    def purge_job(self, job_id: str, docker_image: str | None = None) -> None:
        """Remove all Docker resources created for a job: containers, compose project, images."""
        # Kill sandbox/dockerfile/docker-run containers by label
        self.kill_container(job_id)

        # Kill compose project containers
        self.kill_compose(job_id)

        client = docker.from_env()

        # Remove ALL images tagged with this job's ID prefix
        # The build step creates tags like "gitdeploy/{name}:{job_id[:8]}"
        job_prefix = job_id[:8]
        try:
            for image in client.images.list():
                for tag in (image.tags or []):
                    if job_prefix in tag:
                        try:
                            client.images.remove(tag, force=True)
                        except Exception:
                            pass
        except Exception:
            pass

        # Also try the legacy tag format
        built_image_tag = f"gitdeploy_img_{job_prefix}:latest"
        try:
            client.images.remove(built_image_tag, force=True)
        except Exception:
            pass

        # Remove the pulled Docker Hub image if one was tracked for this job
        # Only remove if no other running container is using it
        if docker_image:
            try:
                in_use = any(
                    c.image.tags and any(docker_image in t for t in c.image.tags)
                    for c in client.containers.list()
                )
                if not in_use:
                    client.images.remove(docker_image, force=False)
            except Exception:
                pass

        # Prune all dangling images (<none> intermediates left by failed/multi-attempt builds)
        try:
            client.images.prune(filters={"dangling": True})
        except Exception:
            pass
