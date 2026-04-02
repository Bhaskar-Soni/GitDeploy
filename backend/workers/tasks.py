"""Celery task definitions for the GitDeploy job pipeline."""

import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import git
import redis
import requests
from sqlalchemy import select

from analyzer.db_detector import DBDetector, DBRequirement
from analyzer.port_detector import PortDetector
from analyzer.repo_analyzer import AnalysisResult, RepoAnalyzer
from ai.db_advisor import DBAAdvisor
from ai.dockerfile_ai import DockerfileAI
from ai.dockerfile_cache import lookup_cached_dockerfile, save_cached_dockerfile
from core.config import settings
from db.database import get_sync_session
from db.models import (
    AppType,
    DBType,
    DetectionSource,
    InstallSource,
    Job,
    JobDatabase,
    JobDBStatus,
    JobLog,
    JobStatus,
    LogStream,
)
from runner.credential_manager import CredentialManager, DBInfo
from runner.db_provisioner import DBProvisioner
from runner.docker_runner import DockerRunner
from runner.network_manager import NetworkManager
from runner.proxy_manager import ProxyManager
from runner.security import SecurityChecker
from workers.celery_app import celery_app

# Redis client for pub/sub log streaming
_redis_client: Optional[redis.Redis] = None

# Default app TTL: 30 minutes
APP_TTL_MINUTES = 30

# ── Well-known Docker Hub images (owner/repo → image, internal_port) ──────────
# These let us skip AI + dockerfile build entirely for popular projects.
_KNOWN_DOCKER_IMAGES: dict[str, tuple[str, int]] = {
    "grafana/grafana": ("grafana/grafana:latest", 3000),
    "prometheus/prometheus": ("prom/prometheus:latest", 9090),
    "portainer/portainer-ce": ("portainer/portainer-ce:latest", 9000),
    "gitea/gitea": ("gitea/gitea:latest", 3000),
    "nextcloud/nextcloud": ("nextcloud:latest", 80),
    "nocodb/nocodb": ("nocodb/nocodb:latest", 8080),
    "appsmith-org/appsmith": ("index.docker.io/appsmith/appsmith-ce:latest", 80),
    "mattermost/mattermost-server": ("mattermost/mattermost-team-edition:latest", 8065),
    "netdata/netdata": ("netdata/netdata:latest", 19999),
    "dozzle-dev/dozzle": ("amir20/dozzle:latest", 8080),
    "louislam/uptime-kuma": ("louislam/uptime-kuma:latest", 3001),
    "gethomepage/homepage": ("ghcr.io/gethomepage/homepage:latest", 3000),
    "filebrowser/filebrowser": ("filebrowser/filebrowser:latest", 80),
    "linuxserver/heimdall": ("lscr.io/linuxserver/heimdall:latest", 80),
    "corentinth/it-tools": ("corentinth/it-tools:latest", 80),
    "requarks/wiki": ("requarks/wiki:2", 3000),
    "outline/outline": ("outlinewiki/outline:latest", 3000),
    "hoppscotch/hoppscotch": ("hoppscotch/hoppscotch:latest", 3000),
    "calcom/cal.com": ("calcom/cal.com:latest", 3000),
    "formbricks/formbricks": ("formbricks/formbricks:latest", 3000),
    "n8n-io/n8n": ("n8nio/n8n:latest", 5678),
    "makeplane/plane": ("makeplane/plane-frontend:latest", 3000),
    "meilisearch/meilisearch": ("getmeili/meilisearch:latest", 7700),
    "typesense/typesense": ("typesense/typesense:latest", 8108),
    "supertokens/supertokens-core": ("registry.supertokens.io/supertokens/supertokens-postgresql:latest", 3567),
}


def _extract_readme_usage(repo_path: str, start_command: Optional[str] = None) -> str:
    """Extract usage instructions from README without AI. Looks for Usage/Example sections."""
    readme_path = None
    for name in ("README.md", "readme.md", "README.rst", "README.txt", "README"):
        p = os.path.join(repo_path, name)
        if os.path.isfile(p):
            readme_path = p
            break
    if not readme_path:
        if start_command:
            return f"CLI Application\n\nRun:\n    {start_command}"
        return ""

    try:
        with open(readme_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return ""

    # Find Usage/Example/How to use sections in markdown
    sections = re.split(r'^#{1,3}\s+', content, flags=re.MULTILINE)
    usage_text = ""
    for section in sections:
        header_line = section.split("\n", 1)[0].lower().strip()
        if any(kw in header_line for kw in ("usage", "example", "how to use", "how to run", "getting started", "quick start")):
            body = section.split("\n", 1)[1] if "\n" in section else ""
            # Extract up to ~600 chars of the section
            usage_text = body.strip()[:600]
            break

    if not usage_text and start_command:
        return f"CLI Application\n\nRun:\n    {start_command}"
    if not usage_text:
        return ""

    # Clean up: keep code blocks and text, trim excessive whitespace
    lines = usage_text.split("\n")
    cleaned = "\n".join(l for l in lines if l.strip())[:500]
    return cleaned


def _probe_dockerhub_image(image_name: str, repo_path: str) -> tuple[Optional[str], Optional[int]]:
    """Check if owner/repo exists on Docker Hub by scanning the README for docker run commands.

    Returns (image, port) or (None, None).
    """
    # Scan README for `docker run ... image_name` patterns
    for readme in ["README.md", "readme.md", "README.rst", "README"]:
        readme_path = os.path.join(repo_path, readme)
        if not os.path.isfile(readme_path):
            continue
        try:
            with open(readme_path, encoding="utf-8", errors="replace") as f:
                content = f.read(5000)
        except OSError:
            continue

        # Match: docker run ... -p PORT:PORT ... image[:tag]
        pattern = re.compile(
            r"docker\s+run\s+[^`\n]*?-p\s+(\d+):(\d+)[^`\n]*?\s+([\w.\-/]+(?::[\w.\-]+)?)",
            re.IGNORECASE,
        )
        for m in pattern.finditer(content):
            host_port_str, container_port_str, found_image = m.group(1), m.group(2), m.group(3)
            # Accept if image name matches owner/repo loosely
            owner_repo_lower = image_name.lower()
            found_lower = found_image.lower().split("/")[-1].split(":")[0]
            owner_part = owner_repo_lower.split("/")[-1]
            if found_lower == owner_part or owner_repo_lower in found_image.lower():
                try:
                    port = int(container_port_str)
                except ValueError:
                    port = 3000
                return found_image, port

    return None, None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.REDIS_URL)
    return _redis_client


def emit_log(job_id: str, message: str, stream: str = "system") -> None:
    """Save a log entry to the database AND publish to Redis for live streaming."""
    stream_enum = {
        "stdout": LogStream.STDOUT,
        "stderr": LogStream.STDERR,
        "system": LogStream.SYSTEM,
    }.get(stream, LogStream.SYSTEM)

    now = datetime.now(timezone.utc)

    try:
        with get_sync_session() as session:
            log_entry = JobLog(
                job_id=job_id,
                stream=stream_enum,
                message=message,
                timestamp=now,
            )
            session.add(log_entry)
            session.commit()
    except Exception:
        pass

    try:
        payload = json.dumps({
            "stream": stream,
            "message": message,
            "timestamp": now.isoformat(),
        })
        _get_redis().publish(f"job_logs:{job_id}", payload)
    except Exception:
        pass


def update_job_status(job_id: str, status: JobStatus, **kwargs) -> None:
    """Update job status and optional fields in the database."""
    with get_sync_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = status
            for key, value in kwargs.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            session.commit()


def _validate_repo_size(repo_url: str) -> None:
    """Check repo size via GitHub API before cloning."""
    parts = repo_url.rstrip("/").split("/")
    if len(parts) >= 2:
        owner, repo = parts[-2], parts[-1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        try:
            resp = requests.get(
                f"https://api.github.com/repos/{owner}/{repo}",
                timeout=10,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if resp.status_code == 200:
                size_kb = resp.json().get("size", 0)
                size_mb = size_kb / 1024
                if size_mb > settings.MAX_REPO_SIZE_MB:
                    raise ValueError(
                        f"Repository size ({size_mb:.0f}MB) exceeds limit ({settings.MAX_REPO_SIZE_MB}MB)"
                    )
        except requests.RequestException:
            pass


@celery_app.task(bind=True, name="process_repo_job", max_retries=0)
def process_repo_job(self, job_id: str) -> None:
    """Main pipeline: clone → analyze → provision DB → install → run app → expose."""
    repo_dir: Optional[str] = None
    network_name: Optional[str] = None
    needs_database = False
    db_infos: list[tuple[str, DBInfo]] = []
    app_container_id: Optional[str] = None
    deploy_method: str = "sandbox"

    try:
        # Guard against race: give DB up to 2s to commit if job not found immediately
        import time as _time
        for _attempt in range(4):
            with get_sync_session() as session:
                _job = session.get(Job, job_id)
            if _job is not None:
                break
            _time.sleep(0.5)
        if _job is None:
            raise RuntimeError(f"Job {job_id} not found in database")

        with get_sync_session() as session:
            job = session.get(Job, job_id)
            repo_url = job.repo_url

        # Parse owner/repo early — used by all strategy paths
        _url_parts = repo_url.rstrip("/").split("/")
        _repo_owner = _url_parts[-2] if len(_url_parts) >= 2 else "unknown"
        _repo_name = _url_parts[-1].removesuffix(".git")

        # ── Fast path: known Docker Hub image → skip cloning entirely ──────────
        repo_key = f"{_repo_owner}/{_repo_name}".lower()
        known_image, known_port = _KNOWN_DOCKER_IMAGES.get(repo_key, (None, None))

        if known_image:
            emit_log(job_id, f"Known app: using pre-built Docker Hub image {known_image}")
            update_job_status(
                job_id, JobStatus.INSTALLING,
                started_at=datetime.now(timezone.utc),
                detected_stack="docker",
                install_source=InstallSource.CONFIG_FILE,
                ai_confidence=1.0,
                app_type=AppType.WEB,
                app_port=known_port,
                docker_image=known_image,
            )

            runner = DockerRunner()
            proxy_port = ProxyManager.allocate_port()
            proxy_url = ProxyManager.get_proxy_url(proxy_port)

            app_container_id = runner.run_image(
                image=known_image,
                job_id=job_id,
                proxy_port=proxy_port,
                app_port=known_port,
                log_callback=emit_log,
            )

            emit_log(job_id, f"Container started. Waiting for app on port {proxy_port}...")
            wait_host = os.environ.get("DOCKER_HOST_ADDR", "host.docker.internal")
            app_ready = ProxyManager.wait_for_app(wait_host, proxy_port, timeout=120)

            if app_ready:
                expires_at = datetime.now(timezone.utc) + timedelta(minutes=APP_TTL_MINUTES)
                update_job_status(
                    job_id, JobStatus.RUNNING,
                    proxy_port=proxy_port,
                    proxy_url=proxy_url,
                    app_container_id=app_container_id,
                    app_port=known_port,
                    finished_at=None,
                    expires_at=expires_at,
                )
                emit_log(job_id, f"App is live at {proxy_url}")
                emit_log(job_id, f"App will auto-stop in {APP_TTL_MINUTES} minutes")
                _send_running(job_id, proxy_url)
                return
            else:
                raise RuntimeError(f"Known image '{known_image}' failed to start on expected port")

        # ── Step 1: Clone ──────────────────────────────────────
        update_job_status(job_id, JobStatus.CLONING, started_at=datetime.now(timezone.utc))
        emit_log(job_id, "Cloning repository...")

        os.makedirs(settings.GITHUB_CLONE_DIR, exist_ok=True)
        repo_dir = tempfile.mkdtemp(dir=settings.GITHUB_CLONE_DIR)

        git.Repo.clone_from(repo_url, repo_dir, depth=1, single_branch=True)
        emit_log(job_id, "Repository cloned successfully")

        # Persist clone path so purge can clean it up later
        update_job_status(job_id, JobStatus.CLONING, clone_path=repo_dir)

        # ── Step 2: Analyze ────────────────────────────────────
        update_job_status(job_id, JobStatus.ANALYZING)
        emit_log(job_id, "Analyzing repository structure...")

        # ── Strategy 1b: README docker-run scanner ────────────────────────────────
        readme_image, readme_port = _probe_dockerhub_image(
            f"{_repo_owner}/{_repo_name}", repo_dir
        )

        if readme_image:
            emit_log(job_id, f"Found Docker Hub image in README: {readme_image} (port {readme_port})")
            # Verify the image actually exists on Docker Hub before committing
            import docker as _docker_check
            _dclient_check = _docker_check.from_env()
            _image_valid = False
            try:
                _dclient_check.images.get_registry_data(readme_image)
                _image_valid = True
                emit_log(job_id, f"Verified Docker Hub image: {readme_image}")
            except Exception:
                emit_log(job_id, f"Image '{readme_image}' not found on Docker Hub — will build from source", "system")

            if _image_valid:
                static = RepoAnalyzer().analyze(repo_dir)
                analysis = AnalysisResult(
                    detected_stack=static.detected_stack,
                    deploy_method="docker-run",
                    docker_image=readme_image,
                    install_source="config_file",
                    ai_confidence=1.0,
                )
                _dockerhub_port = readme_port
            else:
                readme_image = None  # Image invalid, fall through to build pipeline

        if not readme_image:
            readme_image = None
            _dockerhub_port = 3000

            # ── Strategy 2: Check for docker-compose.yml (always preferred) ──
            _has_compose = any(
                os.path.isfile(os.path.join(repo_dir, f))
                for f in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
            )
            if _has_compose:
                emit_log(job_id, "Found docker-compose.yml — using Docker Compose deployment")
                static = RepoAnalyzer().analyze(repo_dir)
                analysis = AnalysisResult(
                    detected_stack=static.detected_stack,
                    deploy_method="docker-compose",
                    install_source="config_file",
                    ai_confidence=1.0,
                )
            else:
                # ── Strategy 3: AI analysis ──
                df_ai = DockerfileAI()
                ai_strategy = df_ai.choose_deploy_strategy(repo_dir, _repo_owner, _repo_name)

                if ai_strategy and ai_strategy.get("confidence", 0) >= 0.75:
                    strategy = ai_strategy["strategy"]
                    emit_log(job_id, f"AI recommends: {strategy} (confidence {ai_strategy['confidence']:.0%})")
                    emit_log(job_id, f"Reasoning: {ai_strategy.get('reasoning', '')}")
                    static = RepoAnalyzer().analyze(repo_dir)

                    # Validate docker-run: verify the image actually exists on Docker Hub
                    # before committing to it, otherwise fall back to dockerfile/sandbox
                    if strategy == "docker-run":
                        ai_image = ai_strategy.get("docker_image") or f"{_repo_owner}/{_repo_name}:latest"
                        import docker as _docker
                        _dclient = _docker.from_env()
                        try:
                            _dclient.images.get_registry_data(ai_image)
                            emit_log(job_id, f"Verified Docker Hub image: {ai_image}")
                        except Exception:
                            emit_log(job_id, f"Image '{ai_image}' not found on Docker Hub — checking for Dockerfile", "system")
                            has_dockerfile = os.path.isfile(os.path.join(repo_dir, "Dockerfile"))
                            strategy = "dockerfile" if has_dockerfile else "sandbox"
                            ai_image = None
                            emit_log(job_id, f"Falling back to: {strategy}")
                        ai_strategy["docker_image"] = ai_image

                    analysis = AnalysisResult(
                        detected_stack=static.detected_stack,
                        deploy_method=strategy,
                        docker_image=ai_strategy.get("docker_image") or None,
                        install_source="ai_generated",
                        ai_confidence=ai_strategy["confidence"],
                    )
                    _dockerhub_port = ai_strategy.get("app_port", 3000)
                else:
                    # ── Strategy 4: Static analysis (file-based detection) ──
                    emit_log(job_id, "Using static file analysis...")
                    analysis = RepoAnalyzer().analyze(repo_dir)
                    _dockerhub_port = 3000

        emit_log(job_id, f"Detected stack: {analysis.detected_stack}")
        emit_log(job_id, f"Deploy method: {analysis.deploy_method}")
        deploy_method = analysis.deploy_method

        runner = DockerRunner()

        # ── Docker Hub image deployment (fastest — pull & run pre-built image) ─
        if analysis.deploy_method == "docker-run":
            docker_image = analysis.docker_image or f"{_repo_owner}/{_repo_name}:latest"
            app_port = _dockerhub_port or 3000

            emit_log(job_id, f"Using pre-built Docker Hub image: {docker_image}")
            update_job_status(
                job_id, JobStatus.INSTALLING,
                detected_stack=analysis.detected_stack,
                install_source=InstallSource.AI_GENERATED,
                ai_confidence=analysis.ai_confidence,
                app_type=AppType.WEB,
                app_port=app_port,
                docker_image=docker_image,
            )

            proxy_port = ProxyManager.allocate_port()
            proxy_url = ProxyManager.get_proxy_url(proxy_port)

            app_container_id = runner.run_image(
                image=docker_image,
                job_id=job_id,
                proxy_port=proxy_port,
                app_port=app_port,
                log_callback=emit_log,
            )

            emit_log(job_id, f"Container started. Waiting for app on port {proxy_port} (internal: {app_port})...")
            wait_host = os.environ.get("DOCKER_HOST_ADDR", "host.docker.internal")
            # Get container object for direct IP connectivity
            import docker as _dh_docker
            _dh_client = _dh_docker.from_env()
            try:
                _dh_container = _dh_client.containers.get(app_container_id)
            except Exception:
                _dh_container = None
            app_ready = ProxyManager.wait_for_app(
                wait_host, proxy_port, timeout=120,
                container=_dh_container, internal_port=app_port,
            )

            # Port recovery: detect actual listening port if initial check fails
            if not app_ready:
                emit_log(job_id, f"App not responding on port {app_port}. Scanning for actual listening port...", "system")
                time.sleep(5)
                try:
                    if _dh_container:
                        _dh_container.reload()
                    else:
                        _dh_container = _dh_client.containers.get(app_container_id)
                    if _dh_container.status == "running":
                        actual_port = ProxyManager.detect_listening_port(_dh_container)
                        if actual_port and actual_port != app_port:
                            emit_log(job_id, f"Detected app on port {actual_port} (expected {app_port}). Remapping...", "system")
                            app_port = actual_port
                            _dh_container.remove(force=True)
                            proxy_port = ProxyManager.allocate_port()
                            proxy_url = ProxyManager.get_proxy_url(proxy_port)
                            app_container_id = runner.run_image(
                                image=docker_image, job_id=job_id,
                                proxy_port=proxy_port, app_port=app_port,
                                log_callback=emit_log,
                            )
                            app_ready = ProxyManager.wait_for_app(wait_host, proxy_port, timeout=60)
                except Exception:
                    pass

            if app_ready:
                expires_at = datetime.now(timezone.utc) + timedelta(minutes=APP_TTL_MINUTES)
                update_job_status(
                    job_id, JobStatus.RUNNING,
                    proxy_port=proxy_port,
                    proxy_url=proxy_url,
                    app_container_id=app_container_id,
                    app_port=app_port,
                    finished_at=None,
                    expires_at=expires_at,
                )
                emit_log(job_id, f"App is live at {proxy_url}")
                emit_log(job_id, f"App will auto-stop in {APP_TTL_MINUTES} minutes")
                _send_running(job_id, proxy_url)
                repo_dir = None
                return
            else:
                emit_log(job_id, "App did not respond on any detected port.", stream="stderr")
                raise RuntimeError(f"Docker Hub image '{docker_image}' failed to start on expected port")

        # ── Docker-Compose deployment (repo ships its own docker-compose.yml) ──
        if analysis.deploy_method == "docker-compose":
            emit_log(job_id, "Repository has docker-compose.yml — using native Docker Compose deployment")
            update_job_status(
                job_id, JobStatus.INSTALLING,
                detected_stack=analysis.detected_stack,
                install_source=InstallSource.CONFIG_FILE,
                app_type=AppType.WEB,
            )

            try:
                proxy_port = ProxyManager.allocate_port()
                proxy_url = ProxyManager.get_proxy_url(proxy_port)

                app_container_id, internal_port = runner.run_compose(
                    repo_path=repo_dir,
                    job_id=job_id,
                    proxy_port=proxy_port,
                    log_callback=emit_log,
                )

                emit_log(job_id, f"Compose stack started. Waiting for app on port {proxy_port}...")
                wait_host = os.environ.get("DOCKER_HOST_ADDR", "host.docker.internal")
                app_ready = ProxyManager.wait_for_app(wait_host, proxy_port, timeout=180)

                if app_ready:
                    expires_at = datetime.now(timezone.utc) + timedelta(minutes=APP_TTL_MINUTES)
                    update_job_status(
                        job_id, JobStatus.RUNNING,
                        proxy_port=proxy_port,
                        proxy_url=proxy_url,
                        app_container_id=app_container_id,
                        app_port=internal_port,
                        finished_at=None,
                        expires_at=expires_at,
                    )
                    emit_log(job_id, f"App is live at {proxy_url}")
                    emit_log(job_id, f"App will auto-stop in {APP_TTL_MINUTES} minutes")
                    _send_running(job_id, proxy_url)
                    repo_dir = None
                    return
                else:
                    emit_log(job_id, "App did not respond within 180 seconds.", stream="stderr")
                    raise RuntimeError("docker-compose app failed to start")
            except Exception as _compose_err:
                emit_log(job_id, f"Docker Compose failed: {_compose_err}. Falling back to AI Dockerfile generation...", "system")
                # Fall through to sandbox deployment below
                analysis.deploy_method = "sandbox"
                deploy_method = "sandbox"

        # ── Dockerfile deployment (repo ships its own Dockerfile) ─────────────
        if analysis.deploy_method == "dockerfile":
            emit_log(job_id, "Repository has a Dockerfile — building and running it directly")

            _dockerfile_path = os.path.join(repo_dir, "Dockerfile")

            # Detect CLI vs web by reading the Dockerfile
            # If it has EXPOSE → web app. If no EXPOSE → CLI tool (run with tail -f /dev/null)
            try:
                _df_content = open(_dockerfile_path, encoding="utf-8").read()
            except OSError:
                _df_content = ""
            _has_expose = bool(re.search(r"^EXPOSE\s+\d+", _df_content, re.MULTILINE | re.IGNORECASE))

            if _has_expose:
                port_detector = PortDetector()
                app_run_info = port_detector.detect(repo_dir, analysis.detected_stack)
                app_port = app_run_info.port or 3000
                _df_app_type = AppType.WEB
                emit_log(job_id, f"Detected web app on port: {app_port}")
            else:
                app_port = None
                _df_app_type = AppType.CLI
                emit_log(job_id, "No EXPOSE in Dockerfile — treating as CLI tool")

            update_job_status(
                job_id, JobStatus.INSTALLING,
                detected_stack=analysis.detected_stack,
                install_source=InstallSource.CONFIG_FILE,
                app_type=_df_app_type,
                app_port=app_port,
            )

            proxy_port = ProxyManager.allocate_port()
            proxy_url = ProxyManager.get_proxy_url(proxy_port) if _has_expose else None

            # Build image with AI self-healing
            import docker as _docker_mod2
            _client2 = _docker_mod2.from_env(timeout=600)
            _safe_name = re.sub(r'[^a-z0-9._-]', '-', _repo_name.lower())[:50]
            _image_tag2 = f"gitdeploy/{_safe_name}:{job_id[:8]}"
            _container_name2 = f"gitdeploy_app_{job_id[:8]}"
            _MAX_FIX_ATTEMPTS = 3
            _dockerfile_build_ok = False

            for _fix_attempt in range(_MAX_FIX_ATTEMPTS):
                try:
                    # Build the image
                    emit_log(job_id, "Building Docker image from repo Dockerfile...", "stdout")
                    _, build_logs2 = _client2.images.build(
                        path=repo_dir,
                        tag=_image_tag2,
                        rm=True,
                        forcerm=True,
                    )
                    for _entry in build_logs2:
                        _line = (_entry.get("stream") or _entry.get("status") or "").rstrip()
                        if _line:
                            emit_log(job_id, _line, "stdout")
                    _dockerfile_build_ok = True
                    break
                except Exception as _build_err2:
                    import docker as _docker_err_mod2
                    _err_lines_raw2 = []
                    if isinstance(_build_err2, _docker_err_mod2.errors.BuildError) and _build_err2.build_log:
                        for _entry2 in _build_err2.build_log:
                            _l2 = (_entry2.get("stream") or _entry2.get("error") or "").rstrip()
                            if _l2:
                                _err_lines_raw2.append(_l2)
                    else:
                        _err_lines_raw2 = str(_build_err2).splitlines()
                    _err_str2 = "\n".join(_err_lines_raw2[-40:])
                    for _el2 in _err_lines_raw2[-20:]:
                        emit_log(job_id, _el2.strip(), "stderr")
                    if _fix_attempt >= _MAX_FIX_ATTEMPTS - 1:
                        emit_log(job_id, "Repo Dockerfile failed after all fix attempts. Generating a new Dockerfile from scratch...", "system")
                        break
                    emit_log(job_id, f"Build failed (attempt {_fix_attempt + 1}/{_MAX_FIX_ATTEMPTS}). AI is analyzing the error...", "system")
                    with open(_dockerfile_path, "r", encoding="utf-8") as _f2:
                        _current_df2 = _f2.read()
                    try:
                        _fix2 = DockerfileAI().fix_dockerfile(_current_df2, _err_str2, repo_dir)
                    except Exception as _ai_err:
                        emit_log(job_id, f"AI fix unavailable: {_ai_err}", "system")
                        _fix2 = None
                    if _fix2 and _fix2.get("dockerfile"):
                        with open(_dockerfile_path, "w", encoding="utf-8") as _f2:
                            _f2.write(_fix2["dockerfile"])
                        emit_log(job_id, f"AI fix applied: {_fix2.get('explanation', 'Dockerfile updated')}", "system")
                        # Re-read expose flag from fixed Dockerfile
                        _df_content = _fix2["dockerfile"]
                        _has_expose = bool(re.search(r"^EXPOSE\s+\d+", _df_content, re.MULTILINE | re.IGNORECASE))
                    else:
                        emit_log(job_id, "AI could not fix the Dockerfile. Generating a new one from scratch...", "system")
                        break

            if not _dockerfile_build_ok:
                # Fall through to AI-generated Dockerfile
                analysis.deploy_method = "sandbox"
            elif _df_app_type == AppType.CLI:
                # CLI tool — run with tail -f /dev/null for terminal access
                try:
                    _client2.containers.get(_container_name2).remove(force=True)
                except Exception:
                    pass
                _container2 = _client2.containers.run(
                    image=_image_tag2,
                    command=["tail", "-f", "/dev/null"],
                    name=_container_name2,
                    mem_limit="1g",
                    nano_cpus=2_000_000_000,
                    read_only=False,
                    security_opt=["no-new-privileges"],
                    remove=False,
                    detach=True,
                    labels={"gitdeploy_job_id": job_id, "gitdeploy_role": "app"},
                )
                app_container_id = _container2.id
                expires_at = datetime.now(timezone.utc) + timedelta(minutes=APP_TTL_MINUTES)
                # Generate usage instructions from README
                emit_log(job_id, "Generating usage instructions from README...", "system")
                _df_usage = DockerfileAI().generate_usage_instructions(repo_dir, f"{_repo_owner}/{_repo_name}")
                update_job_status(
                    job_id, JobStatus.RUNNING,
                    app_container_id=app_container_id,
                    proxy_port=proxy_port,
                    finished_at=None,
                    expires_at=expires_at,
                    usage_instructions=_df_usage or None,
                )
                emit_log(job_id, "Container is ready. Use the Terminal tab to interact.")
                emit_log(job_id, f"App will auto-stop in {APP_TTL_MINUTES} minutes")
                _send_running(job_id, None)
                repo_dir = None
                return
            else:
                # Web app — run with port mapping and wait for readiness
                try:
                    _client2.containers.get(_container_name2).remove(force=True)
                except Exception:
                    pass
                _container2 = _client2.containers.run(
                    image=_image_tag2,
                    name=_container_name2,
                    mem_limit="1g",
                    nano_cpus=2_000_000_000,
                    read_only=False,
                    security_opt=["no-new-privileges"],
                    remove=False,
                    detach=True,
                    ports={f"{app_port}/tcp": proxy_port},
                    labels={
                        "gitdeploy_job_id": job_id,
                        "gitdeploy_role": "app",
                        "gitdeploy_app_port": str(app_port),
                        "gitdeploy_proxy_port": str(proxy_port),
                    },
                )
                app_container_id = _container2.id
                emit_log(job_id, f"Container started. Waiting for app on port {proxy_port} (internal: {app_port})...")
                wait_host = os.environ.get("DOCKER_HOST_ADDR", "host.docker.internal")
                app_ready = ProxyManager.wait_for_app(
                    wait_host, proxy_port, timeout=180,
                    container=_container2, internal_port=app_port,
                )

                # Port recovery: detect actual listening port if initial check fails
                if not app_ready:
                    emit_log(job_id, f"App not responding on port {app_port}. Scanning for actual listening port...", "system")
                    time.sleep(5)
                    _container2.reload()
                    if _container2.status == "running":
                        actual_port = ProxyManager.detect_listening_port(_container2)
                        if actual_port and actual_port != app_port:
                            emit_log(job_id, f"Detected app on port {actual_port} (expected {app_port}). Remapping...", "system")
                            app_port = actual_port
                            _container2.remove(force=True)
                            proxy_port = ProxyManager.allocate_port()
                            proxy_url = ProxyManager.get_proxy_url(proxy_port)
                            _container2 = _client2.containers.run(
                                image=_image_tag2, name=_container_name2,
                                mem_limit="1g", nano_cpus=2_000_000_000,
                                read_only=False, security_opt=["no-new-privileges"],
                                remove=False, detach=True,
                                ports={f"{app_port}/tcp": proxy_port},
                                labels={"gitdeploy_job_id": job_id, "gitdeploy_role": "app",
                                        "gitdeploy_app_port": str(app_port), "gitdeploy_proxy_port": str(proxy_port)},
                            )
                            app_container_id = _container2.id
                            app_ready = ProxyManager.wait_for_app(wait_host, proxy_port, timeout=60)

                if app_ready:
                    expires_at = datetime.now(timezone.utc) + timedelta(minutes=APP_TTL_MINUTES)
                    update_job_status(
                        job_id, JobStatus.RUNNING,
                        proxy_port=proxy_port,
                        proxy_url=proxy_url,
                        app_container_id=app_container_id,
                        app_port=app_port,
                        finished_at=None,
                        expires_at=expires_at,
                    )
                    emit_log(job_id, f"App is live at {proxy_url}")
                    emit_log(job_id, f"App will auto-stop in {APP_TTL_MINUTES} minutes")
                    _send_running(job_id, proxy_url)
                    repo_dir = None
                    return
                else:
                    emit_log(job_id, "App did not respond on any detected port.", stream="stderr")
                    raise RuntimeError("Dockerfile app failed to start — no response on expected port")

        # ── Sandbox deployment: AI-generated Dockerfile ─────────────────────────
        # Instead of running raw install commands in a generic sandbox, ask the AI
        # to generate a proper Dockerfile that bakes in all dependencies.

        # Detect database requirements first (before building)
        db_detector = DBDetector()
        db_requirements = db_detector.detect(repo_dir)

        primary_req = db_requirements[0] if db_requirements else DBRequirement()
        if (
            primary_req.needs_database
            and primary_req.confidence < settings.DB_DETECTION_CONFIDENCE_THRESHOLD
            and primary_req.confidence >= settings.DB_AI_FALLBACK_CONFIDENCE_THRESHOLD
        ):
            emit_log(job_id, "Database requirement ambiguous. Asking AI for recommendation...")
            advisor = DBAAdvisor()
            ai_reqs = advisor.advise(repo_dir)
            if ai_reqs:
                db_requirements = ai_reqs

        db_requirements = [
            r for r in db_requirements
            if r.needs_database and r.confidence >= settings.DB_AI_FALLBACK_CONFIDENCE_THRESHOLD
        ]
        needs_database = len(db_requirements) > 0

        if needs_database:
            for req in db_requirements:
                emit_log(job_id, f"Database required: {req.db_type} (confidence: {req.confidence:.0%})")
        else:
            emit_log(job_id, "No database required for this project")

        # ── Step 3: Provision databases ────────────────────────
        all_env_vars: dict[str, str] = {}

        if needs_database:
            update_job_status(job_id, JobStatus.PROVISIONING_DB)

            # Check if this job already has provisioned databases (restart scenario)
            _existing_dbs = []
            with get_sync_session() as session:
                _existing_dbs = list(
                    session.execute(
                        select(JobDatabase).where(
                            JobDatabase.job_id == job_id,
                            JobDatabase.status == JobDBStatus.READY,
                        )
                    ).scalars().all()
                )
                # Detach from session so we can use them outside
                for _edb in _existing_dbs:
                    session.expunge(_edb)

            if _existing_dbs:
                # Reuse existing DB containers — check they're still running
                import docker as _docker_check
                _dclient = _docker_check.from_env()
                _reused = False
                for _edb in _existing_dbs:
                    try:
                        _dbc = _dclient.containers.get(_edb.container_id or _edb.container_name)
                        if _dbc.status != "running":
                            _dbc.start()
                        # Rebuild env vars from existing DB record
                        env_map = CredentialManager.build_env_map(
                            _edb.db_type.value,
                            DBInfo(
                                container_id=_edb.container_id,
                                container_name=_edb.container_name,
                                host=_edb.db_host,
                                port=_edb.db_port,
                                db_name=_edb.db_name,
                                user=_edb.db_user,
                                password=CredentialManager.decrypt_password(_edb.db_password),
                            ),
                        )
                        all_env_vars.update(env_map)
                        network_name = _edb.docker_network
                        emit_log(job_id, f"Reusing existing {_edb.db_type.value} at {_edb.db_host}:{_edb.db_port}")
                        _reused = True
                    except Exception:
                        pass  # Container gone — will re-provision below

                if _reused:
                    emit_log(job_id, "Credentials reused from previous deployment")
                    needs_database = False  # Skip re-provisioning

        if needs_database:
            emit_log(job_id, "Creating isolated network for this job...")
            network_name = NetworkManager.create(job_id)
            emit_log(job_id, f"Network created: {network_name}")

            provisioner = DBProvisioner()

            for req in db_requirements:
                db_type = req.db_type
                emit_log(job_id, f"Provisioning {db_type} container...")

                credentials = CredentialManager.generate(db_type)
                db_info = provisioner.provision(job_id, db_type, credentials, network_name)
                db_infos.append((db_type, db_info))

                env_map = CredentialManager.build_env_map(db_type, db_info)
                masked_env = CredentialManager.mask_env_vars(env_map)

                with get_sync_session() as session:
                    job_db = JobDatabase(
                        job_id=job_id,
                        db_type=DBType(db_type),
                        detection_source=DetectionSource(req.detection_source),
                        container_id=db_info.container_id,
                        container_name=db_info.container_name,
                        docker_network=network_name,
                        db_name=db_info.db_name,
                        db_host=db_info.host,
                        db_port=db_info.port,
                        db_user=db_info.user,
                        db_password=CredentialManager.encrypt_password(db_info.password),
                        env_vars=masked_env,
                        provisioned_at=datetime.now(timezone.utc),
                        status=JobDBStatus.READY,
                    )
                    session.add(job_db)
                    session.commit()

                all_env_vars.update(env_map)

                emit_log(job_id, f"{db_type} is ready at {db_info.host}:{db_info.port}")

            emit_log(job_id, "Credentials injected into app environment automatically")

        # Parse .env.example if present — auto-populate placeholder env vars
        _env_example = os.path.join(repo_dir, ".env.example")
        if not os.path.isfile(_env_example):
            _env_example = os.path.join(repo_dir, ".env.sample")
        if os.path.isfile(_env_example):
            try:
                with open(_env_example, "r", encoding="utf-8", errors="replace") as _ef:
                    for _line in _ef:
                        _line = _line.strip()
                        if not _line or _line.startswith("#"):
                            continue
                        if "=" in _line:
                            _k, _, _v = _line.partition("=")
                            _k = _k.strip()
                            _v = _v.strip().strip("'\"")
                            if _k and _k not in all_env_vars:
                                all_env_vars[_k] = _v or "placeholder"
                emit_log(job_id, f"Loaded {len(all_env_vars)} env vars from .env.example")
            except Exception:
                pass

        # ── Step 4: Generate Dockerfile (cache → template → AI) ─────────────
        emit_log(job_id, "Generating Dockerfile for this project...")

        dockerfile_result = None
        _dockerfile_source = "ai"  # track where dockerfile came from

        # 1) Check cache — reuse a known-good Dockerfile from previous builds
        _cached = lookup_cached_dockerfile(repo_dir, analysis.detected_stack)
        if _cached:
            emit_log(job_id, f"Found cached Dockerfile (learned from {_cached['source_repo']}, "
                     f"{_cached['success_count']} past successes) — reusing", "system")
            dockerfile_result = _cached
            _dockerfile_source = "cache"

        # 2) Try hardcoded template — instant, no AI needed
        if not dockerfile_result or not dockerfile_result.get("dockerfile"):
            from ai.dockerfile_templates import generate_template
            _template = generate_template(repo_dir, analysis.detected_stack)
            if _template and _template.get("dockerfile"):
                emit_log(job_id, f"Using optimized template for {analysis.detected_stack} (instant, no AI call)", "system")
                dockerfile_result = _template
                _dockerfile_source = "template"

        # 3) AI generation — last resort, handles unusual stacks
        if not dockerfile_result or not dockerfile_result.get("dockerfile"):
            emit_log(job_id, "Calling AI to generate Dockerfile (may take a moment)...")
            try:
                df_ai = DockerfileAI()
            except RuntimeError as _ai_err:
                raise RuntimeError(f"AI not configured: {_ai_err}")
            try:
                dockerfile_result = df_ai.generate_dockerfile(repo_dir, analysis.detected_stack)
            except Exception as _ai_err:
                emit_log(job_id, f"AI error: {_ai_err}", "stderr")
                raise RuntimeError(f"AI failed to generate Dockerfile: {_ai_err}")

        if not dockerfile_result or not dockerfile_result.get("dockerfile"):
            raise RuntimeError("Could not generate Dockerfile — check AI provider settings and API key")

        # Write the AI-generated Dockerfile to the repo
        _gen_dockerfile_path = os.path.join(repo_dir, "Dockerfile")
        with open(_gen_dockerfile_path, "w", encoding="utf-8") as f:
            f.write(dockerfile_result["dockerfile"])

        _gen_app_type = dockerfile_result.get("app_type", "cli")
        _gen_port = dockerfile_result.get("app_port")
        _gen_start_cmd = dockerfile_result.get("start_command")
        _gen_usage = dockerfile_result.get("usage_instructions", "")

        # Generate usage instructions via AI if missing
        if not _gen_usage:
            try:
                _usage_ai = DockerfileAI()
                _gen_usage = _usage_ai.generate_usage_instructions(repo_dir, f"{_repo_owner}/{_repo_name}")
            except Exception:
                pass  # Non-critical — deploy continues without usage info

        # Fallback: extract usage from README if AI didn't produce anything
        if not _gen_usage:
            _gen_usage = _extract_readme_usage(repo_dir, _gen_start_cmd)

        app_type_enum = AppType.WEB if _gen_app_type == "web" else AppType.CLI

        emit_log(job_id, f"App type: {_gen_app_type}")
        if _gen_port:
            emit_log(job_id, f"App port: {_gen_port}")
        if _gen_start_cmd:
            emit_log(job_id, f"Start command: {_gen_start_cmd}")

        update_job_status(
            job_id,
            JobStatus.INSTALLING,
            detected_stack=analysis.detected_stack,
            install_source=InstallSource.AI_GENERATED,
            ai_confidence=analysis.ai_confidence,
            app_type=app_type_enum,
            app_port=_gen_port,
            start_command=_gen_start_cmd,
            usage_instructions=_gen_usage,
        )

        # ── Step 4b: Wrap CMD to write .env at container startup ─────────
        # Many Node apps have postinstall scripts that create .env from
        # .env-example with empty/placeholder values. dotenv then reads these
        # at startup, potentially ignoring container env vars.
        # Fix: wrap the CMD so it writes container env vars → .env before the app starts.
        if all_env_vars and _gen_app_type == "web":
            _dockerfile_content = dockerfile_result["dockerfile"]
            _df_lines = _dockerfile_content.split("\n")
            # Find and replace the CMD line with an entrypoint wrapper
            for _i, _l in enumerate(_df_lines):
                _stripped = _l.strip()
                if _stripped.upper().startswith("CMD"):
                    # Extract the original command
                    _orig_cmd = _stripped
                    # Create a startup script that writes .env from env vars, then runs the app
                    _entrypoint = (
                        'RUN printf \'#!/bin/sh\\n'
                        'env | grep -E "^(DB_|DATABASE_|MYSQL_|POSTGRES_|PG|MONGO|REDIS|PORT|HOST|SESSION|SECRET|NODE_ENV)" '
                        '> /app/.env 2>/dev/null || true\\n'
                        'exec "$@"\\n\' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh'
                    )
                    _df_lines.insert(_i, _entrypoint)
                    _df_lines.insert(_i + 1, 'ENTRYPOINT ["/app/entrypoint.sh"]')
                    break
            dockerfile_result["dockerfile"] = "\n".join(_df_lines)
            emit_log(job_id, "Added entrypoint to inject env vars into .env at startup", "system")

        # ── Step 5: Build (with AI self-healing) ────────────────────────
        import docker as _docker_mod
        _client = _docker_mod.from_env(timeout=600)  # 10min timeout for heavy builds
        _safe_name = re.sub(r'[^a-z0-9._-]', '-', _repo_name.lower())[:50]
        _image_tag = f"gitdeploy/{_safe_name}:{job_id[:8]}"
        _container_name = f"gitdeploy_app_{job_id[:8]}"
        _gen_dockerfile_path = os.path.join(repo_dir, "Dockerfile")
        _MAX_BUILD_ATTEMPTS = 3
        _MAX_RT_ATTEMPTS = 3  # runtime fix attempts — fully independent of build attempts

        def _do_build(attempt_num: int):
            """Build the Docker image, returning rich error string on failure."""
            emit_log(job_id, f"Building Docker image (attempt {attempt_num})..." if attempt_num > 1 else "Building Docker image...")
            import docker as _dm
            try:
                _, build_logs = _client.images.build(path=repo_dir, tag=_image_tag, rm=True, forcerm=True)
                for entry in build_logs:
                    line = (entry.get("stream") or entry.get("status") or "").rstrip()
                    if line:
                        emit_log(job_id, line, "stdout")
                return None  # success
            except Exception as _be:
                _err_lines = []
                if isinstance(_be, _dm.errors.BuildError) and _be.build_log:
                    for _e in _be.build_log:
                        _l = (_e.get("stream") or _e.get("error") or "").rstrip()
                        if _l:
                            _err_lines.append(_l)
                else:
                    _err_lines = str(_be).splitlines()
                _err_str = "\n".join(_err_lines[-40:])
                for _el in _err_lines[-20:]:
                    emit_log(job_id, _el.strip(), "stderr")
                return _err_str

        # ── Build loop ──
        for _ba in range(_MAX_BUILD_ATTEMPTS):
            _build_err_str = _do_build(_ba + 1)
            if _build_err_str is None:
                break  # build succeeded
            if _ba >= _MAX_BUILD_ATTEMPTS - 1:
                raise RuntimeError(f"Docker build failed after {_MAX_BUILD_ATTEMPTS} attempts")
            emit_log(job_id, f"Build failed (attempt {_ba + 1}). Analyzing error...", "system")
            with open(_gen_dockerfile_path, "r", encoding="utf-8") as _f:
                _cur_df = _f.read()

            # Try known fix first (instant, no AI call needed)
            from ai.dockerfile_ai import apply_known_fix as _apply_known_fix
            _bknown = _apply_known_fix(_cur_df, _build_err_str)
            if _bknown:
                _bfixed, _bexpl = _bknown
                with open(_gen_dockerfile_path, "w", encoding="utf-8") as _f:
                    _f.write(_bfixed)
                emit_log(job_id, f"Known fix applied: {_bexpl}", "system")
            else:
                # AI fix — init df_ai if not yet created (e.g. template was used)
                try:
                    df_ai
                except NameError:
                    df_ai = DockerfileAI()
                emit_log(job_id, "AI analyzing build error...", "system")
                _bfix = df_ai.fix_dockerfile(_cur_df, _build_err_str, repo_dir)
                if _bfix and _bfix.get("dockerfile"):
                    _bfixed = _bfix["dockerfile"]
                    with open(_gen_dockerfile_path, "w", encoding="utf-8") as _f:
                        _f.write(_bfixed)
                    _bexpl = _bfix.get("explanation", "Dockerfile updated")
                    emit_log(job_id, f"AI build fix applied: {_bexpl}", "system")
                    try:
                        from ai.error_kb import kb as _kb
                        _kb.learn(_build_err_str, _cur_df, _bfixed, _bexpl)
                    except Exception:
                        pass
                else:
                    raise RuntimeError(f"Docker build failed and AI could not fix")

        emit_log(job_id, "Docker image built successfully")

        # Clean up dangling images from failed build attempts
        try:
            _client.images.prune(filters={"dangling": True})
        except Exception:
            pass

        # ── Step 6: Run & Verify (independent runtime fix loop) ─────────
        try:
            _client.containers.get(_container_name).remove(force=True)
        except Exception:
            pass

        if _gen_app_type == "web" and _gen_port:
            # Web app — start container, wait for response, auto-recover if wrong port
            proxy_port = ProxyManager.allocate_port()
            proxy_url = ProxyManager.get_proxy_url(proxy_port)
            _current_app_port = int(_gen_port)

            def _start_web_container(app_port: int, p_port: int, net: str = None):
                """Start (or restart) the web container with given port mapping."""
                try:
                    _client.containers.get(_container_name).remove(force=True)
                except Exception:
                    pass
                _run_kwargs = dict(
                    image=_image_tag, name=_container_name,
                    environment=all_env_vars or {},
                    mem_limit="1g", nano_cpus=2_000_000_000,
                    read_only=False, security_opt=["no-new-privileges"],
                    remove=False, detach=True,
                    ports={f"{app_port}/tcp": p_port},
                    labels={"gitdeploy_job_id": job_id, "gitdeploy_role": "app",
                            "gitdeploy_app_port": str(app_port), "gitdeploy_proxy_port": str(p_port)},
                )
                if net:
                    _run_kwargs["network"] = net
                _c = _client.containers.run(**_run_kwargs)
                return _c

            container = _start_web_container(_current_app_port, proxy_port, network_name)
            app_container_id = container.id
            emit_log(job_id, f"Container started. Waiting for app on port {proxy_port} (internal: {_current_app_port})...")
            wait_host = os.environ.get("DOCKER_HOST_ADDR", "host.docker.internal")
            app_ready = ProxyManager.wait_for_app(
                wait_host, proxy_port, timeout=120,
                container=container, internal_port=_current_app_port,
            )

            # ── Port recovery: if app didn't respond, check what port it's ACTUALLY listening on ──
            if not app_ready:
                # Dump container logs so we can see why the app isn't starting
                try:
                    container.reload()
                    _app_logs = container.logs(tail=40).decode("utf-8", errors="replace")
                    if _app_logs.strip():
                        emit_log(job_id, f"App container logs:\n{_app_logs[:1500]}", "stderr")
                except Exception:
                    pass

                emit_log(job_id, f"App not responding on port {_current_app_port}. Scanning for actual listening port...", "system")
                time.sleep(5)
                container.reload()
                if container.status == "running":
                    actual_port = ProxyManager.detect_listening_port(container)
                    if actual_port and actual_port != _current_app_port:
                        emit_log(job_id, f"Detected app listening on port {actual_port} (expected {_current_app_port}). Remapping...", "system")
                        _current_app_port = actual_port
                        proxy_port = ProxyManager.allocate_port()
                        proxy_url = ProxyManager.get_proxy_url(proxy_port)
                        container = _start_web_container(_current_app_port, proxy_port, network_name)
                        app_container_id = container.id
                        emit_log(job_id, f"Remapped: container port {_current_app_port} → host port {proxy_port}")
                        app_ready = ProxyManager.wait_for_app(
                            wait_host, proxy_port, timeout=60,
                            container=container, internal_port=_current_app_port,
                        )
                    elif not actual_port:
                        emit_log(job_id, "No listening port detected yet. Waiting another 60 seconds...", "system")
                        app_ready = ProxyManager.wait_for_app(
                            wait_host, proxy_port, timeout=60,
                            container=container, internal_port=_current_app_port,
                        )
                        if not app_ready:
                            container.reload()
                            if container.status == "running":
                                actual_port = ProxyManager.detect_listening_port(container)
                                if actual_port and actual_port != _current_app_port:
                                    emit_log(job_id, f"Late detection: app on port {actual_port}. Remapping...", "system")
                                    _current_app_port = actual_port
                                    proxy_port = ProxyManager.allocate_port()
                                    proxy_url = ProxyManager.get_proxy_url(proxy_port)
                                    container = _start_web_container(_current_app_port, proxy_port, network_name)
                                    app_container_id = container.id
                                    app_ready = ProxyManager.wait_for_app(
                                        wait_host, proxy_port, timeout=60,
                                        container=container, internal_port=_current_app_port,
                                    )
                else:
                    # Container crashed — logs already dumped above
                    pass

            if app_ready:
                expires_at = datetime.now(timezone.utc) + timedelta(minutes=APP_TTL_MINUTES)
                update_job_status(job_id, JobStatus.RUNNING, proxy_port=proxy_port, proxy_url=proxy_url,
                                  app_container_id=app_container_id, finished_at=None, expires_at=expires_at,
                                  app_port=_current_app_port)
                emit_log(job_id, f"App is live at {proxy_url}")
                emit_log(job_id, f"App will auto-stop in {APP_TTL_MINUTES} minutes")
                _send_running(job_id, proxy_url)
                # Cache successful Dockerfile for future similar repos
                try:
                    with open(_gen_dockerfile_path, "r", encoding="utf-8") as _cf:
                        save_cached_dockerfile(
                            repo_dir, analysis.detected_stack, _cf.read(),
                            start_command=_gen_start_cmd, app_type="web",
                            app_port=_current_app_port, repo_name=f"{_repo_owner}/{_repo_name}",
                        )
                except Exception:
                    pass
                repo_dir = None; network_name = None; needs_database = False
                return
            else:
                emit_log(job_id, f"App did not respond on any detected port after extended wait.", stream="stderr")
                raise RuntimeError("Web app failed to start — no response on expected port")

        else:
            # CLI app — runtime verify loop with its own independent counter
            proxy_port = ProxyManager.allocate_port()
            _dep_error_patterns = [
                "ModuleNotFoundError", "ImportError", "No module named",
                "cannot import name", "Error: Cannot find module",
                "MODULE_NOT_FOUND", "Could not find or load main class",
                "libGL", "cannot open shared object",
                "Traceback (most recent call last)",
                "command not found",
                "No such file or directory",
                "SyntaxError",
                "Permission denied",
                "not recognized as",
                "error: unrecognized arguments",
            ]
            _verified = False  # Track whether verification actually succeeded

            for _rt in range(_MAX_RT_ATTEMPTS):
                # Run the container
                try:
                    _client.containers.get(_container_name).remove(force=True)
                except Exception:
                    pass
                _cli_kwargs = dict(
                    image=_image_tag, command=["tail", "-f", "/dev/null"],
                    name=_container_name, environment=all_env_vars or {},
                    mem_limit="1g", nano_cpus=2_000_000_000,
                    read_only=False, security_opt=["no-new-privileges"],
                    remove=False, detach=True,
                    labels={"gitdeploy_job_id": job_id, "gitdeploy_role": "app"},
                )
                if network_name:
                    _cli_kwargs["network"] = network_name
                container = _client.containers.run(**_cli_kwargs)
                app_container_id = container.id

                # Verify
                time.sleep(2)

                # Determine what to verify
                _is_python_cli = False
                _main_file = None

                if _gen_start_cmd:
                    _start_parts = _gen_start_cmd.strip().split()
                    _is_python_cli = _start_parts[0] in ("python", "python3") and len(_start_parts) > 1
                    if _is_python_cli:
                        _main_file = _start_parts[1]

                # For Python stacks without a start command, find a .py file to verify imports
                if not _gen_start_cmd and analysis.detected_stack in ("python-pip", "python-poetry", "python-conda"):
                    _is_python_cli = True
                    # Find the main .py file inside the container
                    _find_exec = container.exec_run(
                        ["bash", "-c", 'cd /app && for f in app.py main.py cli.py run.py; do [ -f "$f" ] && echo "$f" && exit; done; '
                         'grep -rl "if __name__" /app/*.py 2>/dev/null | head -1 | sed "s|/app/||"; '
                         'ls /app/*.py 2>/dev/null | grep -v setup.py | grep -v conftest.py | grep -v __init__.py | head -1 | sed "s|/app/||"'],
                        demux=False,
                    )
                    _found = (_find_exec.output or b"").decode("utf-8", errors="replace").strip().split("\n")[0].strip()
                    if _found and _found.endswith(".py"):
                        _main_file = _found
                    else:
                        _verified = True  # No .py files to verify
                        break

                if not _gen_start_cmd and not _is_python_cli:
                    _verified = True  # Non-Python with no start cmd — nothing to verify
                    break

                if _is_python_cli and _main_file:
                    _test_cmd = f'cd /app && python -c "import py_compile; py_compile.compile(\\"{_main_file}\\", doraise=True)" 2>&1 && python -c "import ast, sys; tree = ast.parse(open(\\"{_main_file}\\").read()); imports = [n.names[0].name if isinstance(n, ast.Import) else n.module for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) and (n.module if isinstance(n, ast.ImportFrom) else True)]; [__import__(i.split(\\".\\")[0]) for i in imports if i and not i.startswith(\\".\\")]" 2>&1'
                    emit_log(job_id, f"[verify {_rt+1}/{_MAX_RT_ATTEMPTS}] Checking imports for {_main_file}", "system")
                elif _gen_start_cmd:
                    _test_cmd = f"(timeout 10 {_gen_start_cmd} --help 2>&1 || timeout 10 {_gen_start_cmd} -h 2>&1 || timeout 5 {_gen_start_cmd} --version 2>&1)"
                    emit_log(job_id, f"[verify {_rt+1}/{_MAX_RT_ATTEMPTS}] Running: {_gen_start_cmd} --help", "system")
                else:
                    _verified = True
                    break

                _exec = container.exec_run(["bash", "-c", _test_cmd], demux=False)
                _test_out = (_exec.output or b"").decode("utf-8", errors="replace")

                # For Python import checks: exit code 0 means all imports resolved
                if _is_python_cli and _exec.exit_code == 0:
                    emit_log(job_id, f"[verify] All imports OK ✓")
                    _verified = True
                    break

                _has_dep_error = any(e in _test_out for e in _dep_error_patterns)

                if not _has_dep_error:
                    emit_log(job_id, f"[verify] App works correctly ✓")
                    _verified = True  # Mark verified on success
                    break

                emit_log(job_id, f"[verify] Error detected:\n{_test_out[:600]}", "stderr")
                if _rt >= _MAX_RT_ATTEMPTS - 1:
                    emit_log(job_id, "[verify] Max runtime fix attempts reached. Deployment marked as FAILED.", "stderr")
                    break

                # Fix and rebuild
                container.remove(force=True)
                app_container_id = None
                with open(_gen_dockerfile_path, "r", encoding="utf-8") as _f:
                    _cur_rt_df = _f.read()
                _rt_error = f"Container runs but app crashes:\n{_test_out[:2000]}"

                _fix_applied = False

                # Known fix first (instant)
                from ai.dockerfile_ai import apply_known_fix as _apply_known_fix
                _known = _apply_known_fix(_cur_rt_df, _test_out)
                if _known:
                    _rt_fixed, _rt_expl = _known
                    with open(_gen_dockerfile_path, "w", encoding="utf-8") as _f:
                        _f.write(_rt_fixed)
                    emit_log(job_id, f"[fix] Known fix: {_rt_expl}", "system")

                    # Try building with known fix
                    _rb_err = _do_build(_rt + 2)
                    if _rb_err is None:
                        _fix_applied = True  # Known fix worked
                    else:
                        # Known fix broke the build — revert and fall back to AI
                        emit_log(job_id, f"[fix] Known fix build failed, falling back to AI...", "system")
                        with open(_gen_dockerfile_path, "w", encoding="utf-8") as _f:
                            _f.write(_cur_rt_df)  # Restore original

                if not _fix_applied:
                    # AI fix (either known fix failed or no known fix)
                    try:
                        df_ai
                    except NameError:
                        df_ai = DockerfileAI()
                    emit_log(job_id, "[fix] AI analyzing runtime error...", "system")
                    _rtfix = df_ai.fix_dockerfile(_cur_rt_df, _rt_error, repo_dir)
                    if _rtfix and _rtfix.get("dockerfile"):
                        _rt_fixed = _rtfix["dockerfile"]
                        with open(_gen_dockerfile_path, "w", encoding="utf-8") as _f:
                            _f.write(_rt_fixed)
                        _rt_expl = _rtfix.get("explanation", "Dockerfile updated")
                        emit_log(job_id, f"[fix] AI fix: {_rt_expl}", "system")
                        try:
                            from ai.error_kb import kb as _kb
                            _kb.learn(_test_out, _cur_rt_df, _rt_fixed, _rt_expl)
                        except Exception:
                            pass

                        _rb_err = _do_build(_rt + 2)
                        if _rb_err:
                            emit_log(job_id, f"[fix] AI fix rebuild failed: {_rb_err[:200]}", "stderr")
                            break
                    else:
                        emit_log(job_id, "[fix] AI could not determine fix.", "system")
                        break
                # continue loop → re-run and re-verify

            # Only mark RUNNING if verification succeeded
            if _verified:
                expires_at = datetime.now(timezone.utc) + timedelta(minutes=APP_TTL_MINUTES)
                update_job_status(
                    job_id, JobStatus.RUNNING,
                    app_container_id=app_container_id,
                    proxy_port=proxy_port, finished_at=None, expires_at=expires_at,
                )
                emit_log(job_id, "Container is ready. Use the Terminal tab to interact.")
                emit_log(job_id, f"Container will auto-stop in {APP_TTL_MINUTES} minutes")
                _send_running(job_id, None)
                # Cache successful Dockerfile for future similar repos
                try:
                    with open(_gen_dockerfile_path, "r", encoding="utf-8") as _cf:
                        save_cached_dockerfile(
                            repo_dir, analysis.detected_stack, _cf.read(),
                            start_command=_gen_start_cmd, app_type="cli",
                            repo_name=f"{_repo_owner}/{_repo_name}",
                        )
                except Exception:
                    pass
                repo_dir = None; network_name = None; needs_database = False
                return
            else:
                # Verification failed after all attempts
                if app_container_id:
                    try:
                        _client.containers.get(_container_name).remove(force=True)
                    except Exception:
                        pass
                raise RuntimeError("App verification failed after maximum attempts. Check logs for error details.")

    except Exception as e:
        error_msg = str(e)
        is_timeout = "timeout" in error_msg.lower() or "timed out" in error_msg.lower()

        if is_timeout:
            update_job_status(
                job_id, JobStatus.TIMEOUT,
                error_message=error_msg, finished_at=datetime.now(timezone.utc),
            )
            emit_log(job_id, f"Job timed out: {error_msg}", stream="stderr")
            _send_final(job_id, "timeout")
        else:
            update_job_status(
                job_id, JobStatus.FAILED,
                error_message=error_msg, finished_at=datetime.now(timezone.utc),
            )
            emit_log(job_id, f"Error: {error_msg}", stream="stderr")
            _send_final(job_id, "failed")

    finally:
        _is_running = _is_running_status(job_id)

        # Clean up cloned repo only if we didn't leave things running
        if repo_dir and os.path.exists(repo_dir) and not _is_running:
            try:
                shutil.rmtree(repo_dir, ignore_errors=True)
            except Exception:
                pass

        if app_container_id and not _is_running:
            try:
                _runner = DockerRunner()
                if deploy_method == "docker-compose":
                    _runner.kill_compose(job_id)
                else:
                    _runner.kill_container(job_id)
            except Exception:
                pass

        # Preserve databases on failure so restart can reuse them.
        # Only tear down DBs when the job is running (handled by stop/cleanup)
        # or when explicitly deleted. This prevents restart from creating
        # duplicate database containers.
        if needs_database and _is_running:
            # Job succeeded and is running — DBs stay alive (normal path)
            pass
        elif needs_database and not _is_running:
            # Job failed — KEEP database containers alive for restart.
            # They will be cleaned up by cleanup_expired_jobs or explicit delete.
            # Just stop the DB containers to save resources, but don't remove them.
            try:
                import docker as _cleanup_docker
                _cleanup_client = _cleanup_docker.from_env()
                with get_sync_session() as session:
                    stmt = select(JobDatabase).where(
                        JobDatabase.job_id == job_id,
                        JobDatabase.status == JobDBStatus.READY,
                    )
                    dbs = session.execute(stmt).scalars().all()
                    for db_record in dbs:
                        # Stop but don't remove — restart will start them back up
                        try:
                            _dbc = _cleanup_client.containers.get(
                                db_record.container_id or db_record.container_name
                            )
                            _dbc.stop(timeout=10)
                        except Exception:
                            pass
            except Exception:
                pass
            # Do NOT tear down the network — restart needs it
            network_name = None

        if network_name and not _is_running:
            try:
                NetworkManager.remove(network_name)
            except Exception:
                pass


def _is_running_status(job_id: str) -> bool:
    """Check if job is in RUNNING status (app left alive intentionally)."""
    try:
        with get_sync_session() as session:
            job = session.get(Job, job_id)
            return job and job.status == JobStatus.RUNNING
    except Exception:
        return False


def _send_final(job_id: str, status: str) -> None:
    """Send final WebSocket message indicating job completion."""
    try:
        payload = json.dumps({
            "stream": "system",
            "message": f"Job finished with status: {status}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "final": True,
        })
        _get_redis().publish(f"job_logs:{job_id}", payload)
    except Exception:
        pass


def _send_running(job_id: str, proxy_url: Optional[str]) -> None:
    """Send WebSocket message that app is now running."""
    try:
        payload = json.dumps({
            "stream": "system",
            "message": f"App is running" + (f" at {proxy_url}" if proxy_url else ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "running": True,
            "proxy_url": proxy_url,
        })
        _get_redis().publish(f"job_logs:{job_id}", payload)
    except Exception:
        pass


@celery_app.task(name="cleanup_expired_jobs")
def cleanup_expired_jobs() -> None:
    """Periodic task to clean up expired running jobs and stale DB containers."""
    now = datetime.now(timezone.utc)

    with get_sync_session() as session:
        stmt = select(Job).where(
            Job.status == JobStatus.RUNNING,
            Job.expires_at <= now,
        )
        expired_jobs = session.execute(stmt).scalars().all()

        for job in expired_jobs:
            job_id = str(job.id)
            emit_log(job_id, "App session expired. Cleaning up...")

            # Kill app container
            try:
                DockerRunner().kill_container(job_id)
            except Exception:
                pass

            # Tear down DB containers
            try:
                DBProvisioner().teardown(job_id)
            except Exception:
                pass

            # Remove network
            if job.databases:
                for db in job.databases:
                    if db.docker_network:
                        try:
                            NetworkManager.remove(db.docker_network)
                        except Exception:
                            pass
                    if db.status != JobDBStatus.TORN_DOWN:
                        db.status = JobDBStatus.TORN_DOWN
                        db.torn_down_at = now

            # Clean up repo dir
            # (repos in named volume get cleaned up on container removal)

            job.status = JobStatus.SUCCESS
            job.finished_at = now
            job.app_container_id = None

            emit_log(job_id, "Session ended. Resources cleaned up.")
            _send_final(job_id, "success")

        # Also clean up stale DB containers from failed/timed-out jobs
        # (preserved for restart, but if not restarted within 30 min, clean up)
        stale_cutoff = now - timedelta(minutes=APP_TTL_MINUTES)
        stale_stmt = select(Job).where(
            Job.status.in_([JobStatus.FAILED, JobStatus.TIMEOUT]),
            Job.finished_at <= stale_cutoff,
        )
        stale_jobs = session.execute(stale_stmt).scalars().all()

        for job in stale_jobs:
            if job.databases:
                _has_live_dbs = any(db.status == JobDBStatus.READY for db in job.databases)
                if _has_live_dbs:
                    job_id = str(job.id)
                    try:
                        DBProvisioner().teardown(job_id)
                    except Exception:
                        pass
                    for db in job.databases:
                        if db.docker_network:
                            try:
                                NetworkManager.remove(db.docker_network)
                            except Exception:
                                pass
                        if db.status != JobDBStatus.TORN_DOWN:
                            db.status = JobDBStatus.TORN_DOWN
                            db.torn_down_at = now

        session.commit()


@celery_app.task(name="stop_job")
def stop_job(job_id: str) -> None:
    """Stop a running job — container is preserved so it can be restarted quickly."""
    emit_log(job_id, "Stopping app...")

    # Stop (don't destroy) the container so Restart can reuse it
    try:
        DockerRunner().stop_container(job_id)
    except Exception:
        pass

    with get_sync_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = JobStatus.SUCCESS
            job.finished_at = datetime.now(timezone.utc)
            # Keep app_container_id, proxy_port, clone_path so restart can reuse them
            session.commit()

    emit_log(job_id, "App stopped. Click Restart to start again without reinstalling.")
    _send_final(job_id, "success")


@celery_app.task(name="restart_job")
def restart_job_task(job_id: str) -> None:
    """Restart a stopped job by starting its existing container."""
    emit_log(job_id, "Restarting app...")

    with get_sync_session() as session:
        job = session.get(Job, job_id)
        if not job:
            return
        container_id = job.app_container_id
        proxy_port = job.proxy_port
        proxy_url = job.proxy_url
        app_port = job.app_port
        app_type = job.app_type

    # Check if old port is still free, if not allocate a new one
    if proxy_port:
        used_ports = ProxyManager._get_docker_used_ports()
        if proxy_port in used_ports:
            old_port = proxy_port
            proxy_port = ProxyManager.allocate_port()
            proxy_url = ProxyManager.get_proxy_url(proxy_port)
            emit_log(job_id, f"Port {old_port} is in use, allocated new port {proxy_port}", "system")
            # Update the container port mapping requires re-creating the container
            container_id = None  # Force re-deploy with new port

    runner = DockerRunner()

    if container_id and runner.start_container(container_id):
        # CLI apps have no web server — just verify the container is running
        if app_type == AppType.CLI or not proxy_port:
            import docker as _docker
            try:
                _client = _docker.from_env()
                _container = _client.containers.get(container_id)
                _container.reload()
                if _container.status == "running":
                    expires_at = datetime.now(timezone.utc) + timedelta(minutes=APP_TTL_MINUTES)
                    update_job_status(
                        job_id, JobStatus.RUNNING,
                        finished_at=None,
                        expires_at=expires_at,
                    )
                    emit_log(job_id, "Container restarted. Terminal access is available.")
                    _send_running(job_id, None)
                    return
            except Exception as _e:
                emit_log(job_id, f"Could not verify container: {_e} — re-deploying...", "stderr")
        else:
            emit_log(job_id, "Container started. Waiting for app...")
            wait_host = os.environ.get("DOCKER_HOST_ADDR", "host.docker.internal")
            app_ready = ProxyManager.wait_for_app(wait_host, proxy_port, timeout=60)

            if app_ready:
                expires_at = datetime.now(timezone.utc) + timedelta(minutes=APP_TTL_MINUTES)
                update_job_status(
                    job_id, JobStatus.RUNNING,
                    finished_at=None,
                    expires_at=expires_at,
                    proxy_port=proxy_port,
                    proxy_url=proxy_url,
                )
                emit_log(job_id, f"App is live again at {proxy_url}")
                _send_running(job_id, proxy_url)
                return
            else:
                emit_log(job_id, "Container didn't respond — container may have been removed. Re-deploying...", "stderr")
    else:
        emit_log(job_id, "Stopped container not found — re-deploying from scratch...", "system")

    # Fall back to full re-deploy
    process_repo_job.delay(job_id)
