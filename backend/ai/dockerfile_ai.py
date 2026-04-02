"""AI-powered repo analysis, Dockerfile generation, and self-healing.

This module uses the configured AI provider (via AIClient) for all LLM calls.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

def apply_known_fix(dockerfile: str, error_text: str) -> tuple[str, str] | None:
    """Try to fix a Dockerfile using the self-learning error knowledge base.
    Returns (fixed_dockerfile, explanation) or None if no pattern matched.
    """
    from ai.error_kb import kb
    match = kb.lookup(error_text)
    if not match:
        return None
    pip_to_add, apt_to_add, explanation = match
    return _patch_dockerfile(dockerfile, pip_to_add, apt_to_add), explanation


def _patch_dockerfile(dockerfile: str, pip_to_add: list, apt_to_add: list) -> str:
    """Insert pip/apt install steps into a Dockerfile before CMD/ENTRYPOINT.
    pip_to_add entries starting with '__UPGRADE__' use pip install --upgrade --ignore-installed.
    """
    lines = dockerfile.splitlines()
    result = []
    inserted_apt = False

    for line in lines:
        result.append(line)
        if apt_to_add and not inserted_apt and re.match(r"RUN apt-get", line, re.IGNORECASE):
            result.append(f"RUN apt-get update && apt-get install -y {' '.join(apt_to_add)} && rm -rf /var/lib/apt/lists/*")
            inserted_apt = True

    cmd_idx = next((i for i, l in enumerate(result) if re.match(r"CMD|ENTRYPOINT", l, re.IGNORECASE)), len(result))

    # Separate upgrade entries from regular installs
    # Split each entry by whitespace so "requests urllib3>=1.26.0,<3" becomes individual tokens
    upgrade_pkgs = []
    for p in pip_to_add:
        if p.startswith("__UPGRADE__"):
            tokens = p.replace("__UPGRADE__", "").strip().split()
            upgrade_pkgs.extend(tokens)
    regular_pkgs = [p for p in pip_to_add if not p.startswith("__UPGRADE__")]

    def _shell_quote_pkg(pkg: str) -> str:
        """Quote a pip package spec if it contains shell-sensitive characters."""
        if any(c in pkg for c in '<>=!'):
            return f'"{pkg}"'
        return pkg

    if upgrade_pkgs:
        # Force upgrade, ignoring pinned versions from requirements.txt
        quoted = [_shell_quote_pkg(p) for p in upgrade_pkgs]
        up_line = f"RUN pip install --no-cache-dir --upgrade --ignore-installed {' '.join(quoted)}"
        result.insert(cmd_idx, up_line)
        cmd_idx += 1

    if regular_pkgs:
        quoted = [_shell_quote_pkg(p) for p in regular_pkgs]
        pip_line = f"RUN pip install --no-cache-dir {' '.join(quoted)}"
        result.insert(cmd_idx, pip_line)
        cmd_idx += 1

    if apt_to_add and not inserted_apt:
        apt_line = f"RUN apt-get update && apt-get install -y {' '.join(apt_to_add)} && rm -rf /var/lib/apt/lists/*"
        result.insert(cmd_idx, apt_line)

    return "\n".join(result)


from ai.ai_client import AIClient
from runner.security import SecurityChecker

# Directories to skip when building file trees
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "target", "dist", "build", ".tox", ".mypy_cache", ".next"}

# Key files to read for context — ordered by importance
KEY_FILES = [
    "README.md", "readme.md", "README.rst",
    "docker-compose.yml", "docker-compose.yaml",
    "Dockerfile",
    "package.json", "pyproject.toml", "requirements.txt",
    "Cargo.toml", "go.mod", "Makefile",
    ".env.example", ".env.sample",
    "setup.py", "setup.cfg", "Gemfile", "composer.json", "mix.exs",
    "pom.xml", "build.gradle",
]

# How many chars to read per file (README and compose get more)
FILE_READ_LIMITS = {
    "README.md": 3000,
    "readme.md": 3000,
    "README.rst": 3000,
    "docker-compose.yml": 2000,
    "docker-compose.yaml": 2000,
    "Dockerfile": 1500,
    "Makefile": 1500,
    "package.json": 1000,
}
DEFAULT_READ_LIMIT = 800


class DockerfileAI:
    """AI client for deploy strategy, Dockerfile generation, and error fixing.

    Despite the name, this now uses the configured cloud AI provider (Gemini, Claude, etc.)
    """

    def __init__(self):
        self._client = AIClient()

    def choose_deploy_strategy(
        self, repo_path: str, repo_owner: str, repo_name: str
    ) -> dict:
        """Decide the fastest deployment strategy for a repo."""
        key_files = self._read_key_files(repo_path)
        file_tree = self._get_file_tree(repo_path, max_depth=2)

        prompt = f"""You are an expert DevOps engineer. Your job is to decide the FASTEST and SIMPLEST way to deploy the GitHub repository "{repo_owner}/{repo_name}" for testing/preview purposes.

Repository file tree:
{file_tree}

Key files:
{key_files}

Deployment strategy options (ranked from fastest to slowest — prefer the fastest that works):

1. **docker-run** — Pull a pre-built image from Docker Hub and run it. This is BY FAR the fastest option. Use it if:
   - The README shows a `docker run` command like `docker run -d -p PORT:PORT image/name`
   - The repo is a well-known project that publishes images to Docker Hub (e.g., grafana/grafana, nginx, redis, etc.)
   - A Docker Hub image is explicitly mentioned in the README "Quick Start" or "Installation" section

2. **docker-compose** — Use the repo's docker-compose.yml with pre-pulled images (no source build). Use it if:
   - docker-compose.yml exists AND the services use `image:` keys (not `build:` keys)

3. **sandbox** — No Docker image available. The AI will generate a Dockerfile. Use this for repos that:
   - Have requirements.txt, package.json, Cargo.toml, etc. but NO Dockerfile
   - Need dependencies installed from package managers (pip, npm, etc.)

4. **dockerfile** — Build from the repo's Dockerfile. Only use if the repo HAS a Dockerfile and there's no pre-built image.

IMPORTANT RULES:
- If you see `docker run -p PORT:PORT image/name` in the README → ALWAYS choose "docker-run"
- For large projects (grafana, prometheus, nextcloud, etc.) → always prefer "docker-run"
- NEVER recommend "dockerfile" if a pre-built Docker Hub image exists
- docker_image must be a REAL Docker Hub image (e.g., "grafana/grafana:latest")
- Do NOT invent docker images that don't exist
- app_port is the PORT the app listens on INSIDE the container

Return ONLY this JSON:
{{
  "strategy": "docker-run",
  "docker_image": "grafana/grafana:latest",
  "app_port": 3000,
  "confidence": 0.95,
  "reasoning": "README shows docker run -d -p 3000:3000 grafana/grafana"
}}"""

        try:
            result = self._client.generate_json(prompt, max_tokens=256)
            strategy = result.get("strategy", "")
            if strategy not in ("docker-run", "docker-compose", "dockerfile", "sandbox"):
                return {}
            return {
                "strategy": strategy,
                "docker_image": result.get("docker_image", ""),
                "app_port": int(result.get("app_port", 3000)),
                "confidence": float(result.get("confidence", 0.5)),
                "reasoning": result.get("reasoning", ""),
            }
        except Exception as e:
            logger.exception("choose_deploy_strategy failed: %s", e)
            return {}

    def generate_install_commands(self, repo_path: str) -> tuple[list[str], float]:
        """Analyze a repo and return (install_commands, confidence)."""
        file_tree = self._get_file_tree(repo_path, max_depth=3)
        key_files = self._read_key_files(repo_path)

        prompt = f"""You are an expert DevOps engineer. Analyze this GitHub repository and return the exact shell commands needed to install its dependencies.

Repository file tree:
{file_tree}

Key files content:
{key_files}

Instructions:
- Read the README carefully — it usually has an "Installation" or "Getting Started" section.
- Prefer commands from the README over guessing.
- Order: install dependencies BEFORE building.
- Do NOT include: start/run commands, sudo, rm -rf, curl|bash, wget|sh.
- Rate confidence: 0.9+ if found in README/config, 0.5-0.7 if inferring, below 0.5 if guessing.

Return ONLY this JSON:
{{
  "commands": ["command1", "command2"],
  "confidence": 0.85,
  "reasoning": "one sentence"
}}"""

        try:
            result = self._client.generate_json(prompt, max_tokens=512)
            raw_commands = result.get("commands", [])
            if not isinstance(raw_commands, list):
                return [], 0.0
            commands = SecurityChecker.filter(raw_commands)
            confidence = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
            return commands, confidence
        except Exception as e:
            logger.exception("generate_install_commands failed: %s", e)
            return [], 0.0

    def generate_dockerfile(self, repo_path: str, detected_stack: str) -> dict:
        """Generate a complete Dockerfile + usage instructions for repos without one."""
        file_tree = self._get_file_tree(repo_path, max_depth=3)
        key_files = self._read_key_files(repo_path)

        # List actual root files so AI knows exactly what exists
        try:
            root_files = sorted(os.listdir(repo_path))[:60]
            file_listing = ", ".join(root_files)
        except OSError:
            file_listing = "(unknown)"

        # Detect versions and system deps for smarter generation
        from ai.dockerfile_templates import (
            detect_python_version, detect_node_version, detect_go_version,
            detect_system_deps,
        )
        _py_ver = detect_python_version(repo_path)
        _node_ver = detect_node_version(repo_path)
        _go_ver = detect_go_version(repo_path)
        _sys_deps = detect_system_deps(repo_path)
        _sys_deps_hint = f"\nDetected system dependencies needed: {', '.join(_sys_deps)}" if _sys_deps else ""

        prompt = f"""You are an expert DevOps engineer. Generate a Dockerfile for this repository. It has NO existing Dockerfile.

Detected stack: {detected_stack or "unknown"}
Files in repo root: {file_listing}
Detected Python version: {_py_ver}
Detected Node.js version: {_node_ver}
Detected Go version: {_go_ver}{_sys_deps_hint}

Repository file tree:
{file_tree}

Key files:
{key_files}

════════════════════════════════════════
STRICT DOCKERFILE RULES — follow exactly
════════════════════════════════════════

BASE IMAGE (use detected versions):
  Python  → python:{_py_ver}-slim
  Node.js → node:{_node_ver}-slim
  Go      → golang:{_go_ver}-bookworm
  Rust    → rust:1.77-slim
  Ruby    → ruby:3.3-slim
  Other   → ubuntu:24.04

STRUCTURE (use this exact order):
  FROM <image>
  WORKDIR /app
  COPY . /app
  [optional: RUN apt-get update && apt-get install -y <pkgs> && rm -rf /var/lib/apt/lists/*]
  [install deps — see below]
  CMD [...]

PYTHON DEPENDENCY INSTALLATION — choose ONE based on what files ACTUALLY EXIST:
  • If requirements.txt EXISTS in root:
      RUN pip install --no-cache-dir -r requirements.txt
  • If pyproject.toml EXISTS (no requirements.txt):
      RUN pip install --no-cache-dir .
      NOTE: This also installs CLI entry points defined in [project.scripts] or [tool.poetry.scripts].
      If pyproject.toml defines a CLI command (e.g. "user-scanner"), use THAT as start_command (not "python file.py").
  • If setup.py EXISTS (no requirements.txt, no pyproject.toml):
      RUN pip install --no-cache-dir .
  • If NEITHER exists (single script or no deps):
      RUN pip install --no-cache-dir <package1> <package2>   ← install only what the script imports
  • DO NOT reference requirements.txt if it does not exist in the file list above
  • If BOTH requirements.txt AND pyproject.toml exist → use requirements.txt for deps, then also run pip install --no-cache-dir -e . for entry points

NODE.JS:
  • If package-lock.json exists: RUN npm ci
  • Otherwise: RUN npm install
  • If package.json has a "build" script: add RUN npm run build AFTER npm install
  • For web apps built with React/Vue/Angular/Next.js/Vite:
    1. Install deps: RUN npm ci (or npm install)
    2. Build: RUN npm run build
    3. Serve: use "npx serve -s build -l PORT" or "npx serve -s dist -l PORT" or the framework's start command
  • Check package.json "scripts.start" — if it exists, use CMD ["npm", "start"]
  • If "scripts.start" doesn't exist but "scripts.build" does, serve the build output

CRITICAL RULES — violations cause build failures:
  ✗ NEVER combine ENV and RUN on the same line (e.g. "RUN ... && ENV ..." is INVALID)
  ✗ NEVER use shell variables like $VERSION or $TAG in pip install commands
  ✗ NEVER use venv — plain pip install is faster and simpler in Docker
  ✗ NEVER reference a file that is not listed in "Files in repo root"
  ✓ Each Dockerfile instruction must be on its own line
  ✓ ENV must be a standalone line: ENV MY_VAR=value
  ✓ ALWAYS quote pip version constraints with < or >: pip install "pkg>=1.0,<2"

APP TYPE:
  • CLI tool (no web server) → CMD ["tail", "-f", "/dev/null"]   so container stays alive for terminal access
  • Web app → EXPOSE <port> and CMD to start the server

USAGE INSTRUCTIONS — read the README "Usage" section and write real examples:
  • Show the actual command with realistic arguments
  • Example for a password tool: "python password-inspector.py MyPassword123\\npython password-inspector.py -h"
  • Example for a scanner: "python scanner.py --target example.com\\npython scanner.py --help"
  • 2-4 lines max, no fluff

Return ONLY this JSON (no markdown, no explanation):
{{
  "dockerfile": "FROM python:3.12-slim\\nWORKDIR /app\\nCOPY . /app\\nRUN pip install --no-cache-dir -r requirements.txt\\nCMD [\\"tail\\", \\"-f\\", \\"/dev/null\\"]",
  "usage_instructions": "python password-inspector.py <password>\\npython password-inspector.py -h",
  "app_type": "cli",
  "app_port": null,
  "start_command": "python password-inspector.py"
}}"""

        try:
            result = self._client.generate_json(prompt, max_tokens=1024)
            dockerfile = result.get("dockerfile", "")
            if not dockerfile or "FROM" not in dockerfile:
                return {}
            return {
                "dockerfile": dockerfile,
                "usage_instructions": result.get("usage_instructions", ""),
                "app_type": result.get("app_type", "cli"),
                "app_port": result.get("app_port"),
                "start_command": result.get("start_command"),
            }
        except Exception as e:
            logger.exception("generate_dockerfile failed: %s", e)
            raise

    def fix_dockerfile(
        self, dockerfile_content: str, error_output: str, repo_path: str
    ) -> dict:
        """Given a Dockerfile and error, generate a fixed version."""
        key_files = self._read_key_files(repo_path)
        # List actual files in root so the AI knows what exists
        try:
            root_files = sorted(os.listdir(repo_path))[:50]
            file_listing = ", ".join(root_files)
        except OSError:
            file_listing = "(unknown)"

        # Get system deps info so AI knows what apt packages might be needed
        from ai.dockerfile_templates import detect_system_deps
        _sys_deps = detect_system_deps(repo_path)
        _sys_deps_hint = f"\nKnown system dependencies for this project's pip packages: {', '.join(_sys_deps)}" if _sys_deps else ""

        prompt = f"""You are an expert DevOps engineer. A Dockerfile failed to build. Fix it.{_sys_deps_hint}

Current Dockerfile:
```
{dockerfile_content}
```

Error output:
```
{error_output[:2000]}
```

Files in repository root: {file_listing}

Key files:
{key_files}

DIAGNOSE AND FIX — read the FULL error carefully, fix only the failing line:

1. "E: Unable to locate package X" → The package name is WRONG. Use correct Debian package names:
   - libGL.so → libgl1  (NOT libgl1-mesa-glx)
   - libegl → libegl1  (NOT libegl1-mesa)
   - Firefox/geckodriver → firefox-esr
   - Chrome → chromium
   - ffmpeg → ffmpeg
   - gcc → gcc
   - libxcb → libxcb1-dev
   - libsm → libsm6
   - libxext → libxext6  (NOT libext6)
   - libxrender → libxrender1
   - libglib → libglib2.0-0
   - imagemagick → imagemagick
   - git → git
   - curl → curl
   ONLY include packages that exist. When unsure, use fewer packages.

2. Shell variable undefined ($VERSION, $TAG, etc.) → remove the version pin entirely.

3. "ENV" inside a RUN command → WRONG. Must be its own Dockerfile instruction line.

4. Python import error (ModuleNotFoundError, ImportError) → add `pip install <package>` for the missing package.
   - selenium_firefox / selenium-wire → pip install selenium-wire  (needs firefox-esr + geckodriver in apt)
   - playwright → pip install playwright && RUN playwright install --with-deps chromium
   - cv2 → pip install opencv-python-headless
   - PIL → pip install Pillow

5. "command not found" error → the CLI entry point wasn't installed. Fix:
   - If pyproject.toml exists → ensure `RUN pip install --no-cache-dir .` is present (installs entry points)
   - If setup.py exists → ensure `RUN pip install --no-cache-dir .` is present
   - Also add `RUN pip install --no-cache-dir -e .` if editable install is needed

6. requirements.txt → if it IS listed in the file listing, ALWAYS keep `pip install -r requirements.txt`.

7. Python 3.12 compatibility issues:
   - urllib3 < 1.26 breaks on Python 3.12 → add: RUN pip install --no-cache-dir --upgrade --ignore-installed requests "urllib3>=1.26.0,<3"
   - distutils removed → add: RUN pip install --no-cache-dir setuptools
   - imp module removed → update the importing package

8. SHELL QUOTING: pip version specs with < or > MUST be quoted in Dockerfile RUN commands:
   ✗ WRONG: RUN pip install urllib3>=1.26.0,<3    (shell reads <3 as file redirect)
   ✓ RIGHT: RUN pip install "urllib3>=1.26.0,<3"  (quotes protect the constraint)

9. npm/yarn postinstall or prepare script fails ("cp: cannot stat", "No such file"):
   The postinstall/prepare script needs files that aren't copied yet.
   FIX: Move "COPY . ." BEFORE "RUN npm ci" / "RUN npm install" so all files are available.
   ✗ WRONG order: COPY package*.json ./ → RUN npm ci → COPY . .   (postinstall can't find files)
   ✓ RIGHT order: COPY . . → RUN npm ci   (all files present when postinstall runs)

10. Node.js engine version mismatch ("Unsupported engine", "required: {{ node: '>=X' }}"):
   Change the base image to match the required version. Use the CLOSEST LTS:
   - Required >=18 → FROM node:18-slim
   - Required >=20 → FROM node:20-slim
   - Required >=22 → FROM node:22-slim

CRITICAL RULES:
- NEVER write "RUN ... && ENV ..." — ENV must be on its own line
- If requirements.txt IS in the file list → keep `pip install -r requirements.txt`
- Only add apt packages you are CERTAIN exist in Debian bookworm
- Output a COMPLETE Dockerfile, not a patch
- For CLI tools: keep CMD ["tail", "-f", "/dev/null"]
- ALWAYS quote pip version constraints containing < or > with double quotes
- For Node.js: if postinstall/prepare scripts exist, ALWAYS COPY . . BEFORE npm install

Return ONLY this JSON:
{{
  "dockerfile": "FROM python:3.12-slim\\nWORKDIR /app\\nCOPY . /app\\nRUN pip install --no-cache-dir requests\\nCMD [\\"tail\\", \\"-f\\", \\"/dev/null\\"]",
  "explanation": "Fixed by ..."
}}"""

        try:
            result = self._client.generate_json(prompt, max_tokens=1024)
            dockerfile = result.get("dockerfile", "")
            if not dockerfile or "FROM" not in dockerfile:
                return {}
            return {
                "dockerfile": dockerfile,
                "explanation": result.get("explanation", "AI applied a fix"),
            }
        except Exception as e:
            logger.exception("fix_dockerfile failed: %s", e)
            return {}

    def generate_usage_instructions(self, repo_path: str, repo_name: str) -> str:
        """Read the README and generate concise usage instructions with real example commands."""
        key_files = self._read_key_files(repo_path)

        prompt = f"""You are a technical writer. A user has deployed the "{repo_name}" CLI tool and needs to know how to use it.

Read the README below and extract the actual usage commands with real examples.

{key_files}

Your task:
1. Find the CLI command name (e.g. sherlock, nmap, ffuf, etc.)
2. Show 3-5 real example commands from the README with realistic arguments
3. Keep it concise — just the commands, no fluff

Return ONLY this JSON:
{{
  "usage_instructions": "Run sherlock to find social media accounts:\\n  sherlock username\\n  sherlock user1 user2 user3\\n  sherlock --timeout 10 username"
}}"""

        try:
            result = self._client.generate_json(prompt, max_tokens=256)
            return result.get("usage_instructions", "")
        except Exception as e:
            logger.exception("generate_usage_instructions failed: %s", e)
            return ""

    def _get_file_tree(self, repo_path: str, max_depth: int = 3) -> str:
        lines: list[str] = []
        self._walk_tree(repo_path, repo_path, lines, max_depth, prefix="")
        return "\n".join(lines[:600])

    def _walk_tree(
        self, base_path: str, current_path: str, lines: list[str],
        max_depth: int, prefix: str, depth: int = 0,
    ) -> None:
        if depth >= max_depth or len(lines) > 600:
            return
        try:
            entries = sorted(os.listdir(current_path))
        except OSError:
            return
        dirs, files = [], []
        for e in entries:
            full = os.path.join(current_path, e)
            if os.path.isdir(full):
                if e not in SKIP_DIRS and not e.startswith("."):
                    dirs.append(e)
            else:
                files.append(e)
        for f in files:
            lines.append(f"{prefix}{f}")
        for d in dirs:
            lines.append(f"{prefix}{d}/")
            self._walk_tree(base_path, os.path.join(current_path, d), lines, max_depth, prefix + "  ", depth + 1)

    def _read_key_files(self, repo_path: str) -> str:
        sections: list[str] = []
        for filename in KEY_FILES:
            filepath = os.path.join(repo_path, filename)
            if not os.path.isfile(filepath):
                continue
            limit = FILE_READ_LIMITS.get(filename, DEFAULT_READ_LIMIT)
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(limit)
            except OSError:
                continue
            sections.append(f"=== {filename} ===\n{content}\n")
        return "\n".join(sections) if sections else "(no key files found)"
