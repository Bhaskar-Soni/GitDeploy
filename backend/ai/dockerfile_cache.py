"""Dockerfile cache — self-learning system that remembers successful builds.

After a successful build, the Dockerfile is cached keyed by a "stack signature"
(hash of key file names + detected stack). Before calling AI, the cache is checked.
If a similar repo was built before, the cached Dockerfile is reused — no AI call needed.
"""

import hashlib
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def compute_stack_signature(repo_path: str, detected_stack: str) -> str:
    """Generate a signature based on repo structure and stack.

    The signature captures WHAT the repo looks like (key files present,
    stack type) so similar repos get the same cached Dockerfile.
    """
    key_files = [
        "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
        "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "Cargo.toml", "go.mod", "Gemfile", "composer.json",
        "Makefile", "CMakeLists.txt", ".env.example",
    ]
    present = []
    for f in key_files:
        if os.path.isfile(os.path.join(repo_path, f)):
            present.append(f)

    # Also check if there's a build script in package.json
    has_build = False
    pkg_json = os.path.join(repo_path, "package.json")
    if os.path.isfile(pkg_json):
        try:
            import json
            with open(pkg_json, "r") as fh:
                pkg = json.load(fh)
                scripts = pkg.get("scripts", {})
                if "build" in scripts:
                    has_build = True
        except Exception:
            pass

    sig_parts = [detected_stack, ",".join(sorted(present))]
    if has_build:
        sig_parts.append("has_build")

    raw = "|".join(sig_parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def lookup_cached_dockerfile(repo_path: str, detected_stack: str) -> Optional[dict]:
    """Check if we have a cached Dockerfile for a similar repo."""
    try:
        sig = compute_stack_signature(repo_path, detected_stack)

        import psycopg2
        db_url = os.environ.get("SYNC_DATABASE_URL", "postgresql://gitdeploy:gitdeploy@postgres:5432/gitdeploy")
        conn = psycopg2.connect(db_url)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT dockerfile, start_command, app_type, app_port, repo_name, success_count "
                "FROM dockerfile_cache WHERE stack_signature = %s AND detected_stack = %s "
                "ORDER BY success_count DESC, updated_at DESC LIMIT 1",
                (sig, detected_stack),
            )
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()

        if row:
            logger.info("Dockerfile cache HIT for sig=%s (from %s, %d successes)", sig, row[4], row[5])
            return {
                "dockerfile": row[0],
                "start_command": row[1],
                "app_type": row[2] or "cli",
                "app_port": row[3],
                "source_repo": row[4],
                "success_count": row[5],
            }
    except Exception as e:
        logger.debug("Dockerfile cache lookup failed: %s", e)
    return None


def save_cached_dockerfile(
    repo_path: str,
    detected_stack: str,
    dockerfile: str,
    start_command: str = None,
    app_type: str = None,
    app_port: int = None,
    repo_name: str = None,
):
    """Save a successful Dockerfile to cache for future similar repos."""
    try:
        sig = compute_stack_signature(repo_path, detected_stack)

        import psycopg2
        import uuid
        db_url = os.environ.get("SYNC_DATABASE_URL", "postgresql://gitdeploy:gitdeploy@postgres:5432/gitdeploy")
        conn = psycopg2.connect(db_url)
        try:
            cur = conn.cursor()

            # Check if we already have an entry for this signature
            cur.execute(
                "SELECT id, success_count FROM dockerfile_cache "
                "WHERE stack_signature = %s AND detected_stack = %s LIMIT 1",
                (sig, detected_stack),
            )
            existing = cur.fetchone()

            if existing:
                cur.execute(
                    "UPDATE dockerfile_cache SET dockerfile = %s, start_command = %s, "
                    "app_type = %s, app_port = %s, success_count = success_count + 1, "
                    "updated_at = NOW() WHERE id = %s",
                    (dockerfile, start_command, app_type, app_port, existing[0]),
                )
            else:
                cur.execute(
                    "INSERT INTO dockerfile_cache "
                    "(id, stack_signature, detected_stack, dockerfile, start_command, app_type, app_port, repo_name) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (str(uuid.uuid4()), sig, detected_stack, dockerfile, start_command, app_type, app_port, repo_name),
                )

            conn.commit()
            cur.close()
        finally:
            conn.close()
        logger.info("Dockerfile cached for sig=%s repo=%s", sig, repo_name)
    except Exception as e:
        logger.debug("Dockerfile cache save failed: %s", e)
