"""Hardcoded Dockerfile templates for common stacks.

These bypass AI entirely for well-known project structures, giving instant
and reliable Dockerfiles. If a template fails at build time, the AI fix loop
still kicks in — so templates are a fast path, not a dead end.

Flow: cache → template → AI generate → AI fix loop
"""

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ── Version detection helpers ─────────────────────────────────────────────────

def detect_python_version(repo_path: str) -> str:
    """Detect required Python version from project config files."""
    # 1. Check pyproject.toml for requires-python
    pyproject = os.path.join(repo_path, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            # requires-python = ">=3.9"  or  python_requires = ">=3.8"
            m = re.search(r'(?:requires-python|python_requires)\s*=\s*["\']([^"\']+)', content)
            if m:
                ver = _parse_version_constraint(m.group(1))
                if ver:
                    return ver
            # [tool.poetry.dependencies] python = "^3.9"
            m = re.search(r'python\s*=\s*["\'][\^~>=<]*(\d+\.\d+)', content)
            if m:
                return m.group(1)
        except OSError:
            pass

    # 2. Check setup.cfg
    setup_cfg = os.path.join(repo_path, "setup.cfg")
    if os.path.isfile(setup_cfg):
        try:
            with open(setup_cfg, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            m = re.search(r'python_requires\s*=\s*([^\n]+)', content)
            if m:
                ver = _parse_version_constraint(m.group(1))
                if ver:
                    return ver
        except OSError:
            pass

    # 3. Check setup.py
    setup_py = os.path.join(repo_path, "setup.py")
    if os.path.isfile(setup_py):
        try:
            with open(setup_py, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(3000)
            m = re.search(r'python_requires\s*=\s*["\']([^"\']+)', content)
            if m:
                ver = _parse_version_constraint(m.group(1))
                if ver:
                    return ver
        except OSError:
            pass

    # 4. Check .python-version file
    pyver_file = os.path.join(repo_path, ".python-version")
    if os.path.isfile(pyver_file):
        try:
            with open(pyver_file, "r") as f:
                ver = f.read().strip()
            m = re.match(r'(\d+\.\d+)', ver)
            if m:
                return m.group(1)
        except OSError:
            pass

    # 5. Check runtime.txt (Heroku-style)
    runtime = os.path.join(repo_path, "runtime.txt")
    if os.path.isfile(runtime):
        try:
            with open(runtime, "r") as f:
                ver = f.read().strip()
            m = re.search(r'python-(\d+\.\d+)', ver)
            if m:
                return m.group(1)
        except OSError:
            pass

    return "3.12"  # default


def detect_node_version(repo_path: str) -> str:
    """Detect required Node.js version from package.json engines field."""
    pkg_json = os.path.join(repo_path, "package.json")
    if os.path.isfile(pkg_json):
        try:
            with open(pkg_json, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            engines = pkg.get("engines", {})
            node_ver = engines.get("node", "")
            if node_ver:
                ver = _parse_version_constraint(node_ver)
                if ver:
                    # Map to closest LTS: 18, 20, 22
                    major = int(ver.split(".")[0])
                    if major <= 18:
                        return "18"
                    elif major <= 20:
                        return "20"
                    else:
                        return "22"
        except (OSError, json.JSONDecodeError):
            pass

    # Check .nvmrc
    nvmrc = os.path.join(repo_path, ".nvmrc")
    if os.path.isfile(nvmrc):
        try:
            with open(nvmrc, "r") as f:
                ver = f.read().strip().lstrip("v")
            m = re.match(r'(\d+)', ver)
            if m:
                major = int(m.group(1))
                if major <= 18:
                    return "18"
                elif major <= 20:
                    return "20"
                else:
                    return "22"
        except OSError:
            pass

    # Check .node-version
    node_ver_file = os.path.join(repo_path, ".node-version")
    if os.path.isfile(node_ver_file):
        try:
            with open(node_ver_file, "r") as f:
                ver = f.read().strip().lstrip("v")
            m = re.match(r'(\d+)', ver)
            if m:
                major = int(m.group(1))
                if major <= 18:
                    return "18"
                elif major <= 20:
                    return "20"
                else:
                    return "22"
        except OSError:
            pass

    return "20"  # default LTS


def detect_go_version(repo_path: str) -> str:
    """Detect Go version from go.mod."""
    go_mod = os.path.join(repo_path, "go.mod")
    if os.path.isfile(go_mod):
        try:
            with open(go_mod, "r", encoding="utf-8") as f:
                content = f.read()
            m = re.search(r'^go\s+(\d+\.\d+)', content, re.MULTILINE)
            if m:
                return m.group(1)
        except OSError:
            pass
    return "1.22"  # default


def _parse_version_constraint(constraint: str) -> Optional[str]:
    """Extract the minimum version from a constraint like '>=3.9,<4' or '^3.10'."""
    constraint = constraint.strip().strip("\"'")
    # Match patterns like >=3.9, ~=3.9, ^3.9, ==3.9, 3.9
    m = re.search(r'(\d+\.\d+)', constraint)
    return m.group(1) if m else None


# ── System dependency mapping ─────────────────────────────────────────────────

# Maps pip packages to the apt packages they need to build/run
PYTHON_SYSTEM_DEPS: dict[str, list[str]] = {
    # Database drivers
    "psycopg2": ["libpq-dev", "gcc"],
    "psycopg2-binary": [],  # binary wheels, no build deps
    "mysqlclient": ["default-libmysqlclient-dev", "gcc", "pkg-config"],
    "pymssql": ["freetds-dev", "gcc"],

    # Image processing
    "pillow": ["libjpeg-dev", "zlib1g-dev", "libfreetype-dev"],
    "opencv-python": ["libgl1", "libglib2.0-0"],
    "opencv-python-headless": ["libgl1", "libglib2.0-0"],
    "opencv-contrib-python": ["libgl1", "libglib2.0-0"],

    # XML/HTML parsing
    "lxml": ["libxml2-dev", "libxslt1-dev", "gcc"],

    # Scientific / numeric
    "numpy": [],  # usually works with wheels
    "scipy": ["gfortran", "libopenblas-dev"],
    "matplotlib": ["libfreetype-dev", "pkg-config"],

    # Crypto
    "cryptography": ["libffi-dev", "gcc"],
    "pynacl": ["libffi-dev", "gcc"],
    "bcrypt": ["libffi-dev", "gcc"],

    # Network / browser
    "playwright": [],  # needs special handling (playwright install)
    "selenium": [],
    "scrapy": ["libxml2-dev", "libxslt1-dev", "libffi-dev", "gcc"],

    # Audio/video
    "pyaudio": ["portaudio19-dev", "gcc"],
    "pydub": ["ffmpeg"],
    "ffmpeg-python": ["ffmpeg"],

    # System
    "python-magic": ["libmagic1"],
    "netifaces": ["gcc"],
    "psutil": ["gcc"],

    # Compression
    "python-snappy": ["libsnappy-dev", "gcc"],
    "brotli": ["gcc"],

    # Other
    "cffi": ["libffi-dev", "gcc"],
    "pygraphviz": ["graphviz", "graphviz-dev", "gcc"],
    "cairosvg": ["libcairo2-dev", "pkg-config", "gcc"],
    "weasyprint": ["libcairo2-dev", "libpango1.0-dev", "libgdk-pixbuf2.0-dev", "libffi-dev"],
    "cairo": ["libcairo2-dev"],
    "git+": ["git"],  # git-based deps
}


def detect_system_deps(repo_path: str) -> list[str]:
    """Scan requirements to find needed system (apt) packages."""
    deps_content = ""

    for req_file in ("requirements.txt", "requirements-dev.txt", "requirements_dev.txt"):
        path = os.path.join(repo_path, req_file)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    deps_content += f.read().lower() + "\n"
            except OSError:
                pass

    # Also check pyproject.toml dependencies
    pyproject = os.path.join(repo_path, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject, "r", encoding="utf-8", errors="replace") as f:
                deps_content += f.read().lower() + "\n"
        except OSError:
            pass

    apt_packages = set()
    for pip_pkg, apt_pkgs in PYTHON_SYSTEM_DEPS.items():
        if pip_pkg == "git+":
            # Special: check for git+https:// URLs
            if "git+" in deps_content:
                apt_packages.update(apt_pkgs)
        elif pip_pkg.lower() in deps_content:
            apt_packages.update(apt_pkgs)

    return sorted(apt_packages)


# ── Node.js helpers ───────────────────────────────────────────────────────────

def detect_node_package_manager(repo_path: str) -> str:
    """Detect which package manager to use."""
    if os.path.isfile(os.path.join(repo_path, "pnpm-lock.yaml")):
        return "pnpm"
    if os.path.isfile(os.path.join(repo_path, "yarn.lock")):
        return "yarn"
    if os.path.isfile(os.path.join(repo_path, "bun.lockb")):
        return "bun"
    return "npm"


def detect_node_framework(repo_path: str) -> Optional[str]:
    """Detect the Node.js framework from package.json dependencies."""
    pkg_json = os.path.join(repo_path, "package.json")
    if not os.path.isfile(pkg_json):
        return None
    try:
        with open(pkg_json, "r", encoding="utf-8") as f:
            pkg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    scripts = pkg.get("scripts", {})

    # Order matters — most specific first
    if "next" in deps:
        return "nextjs"
    if "nuxt" in deps:
        return "nuxt"
    if "@angular/core" in deps:
        return "angular"
    if "svelte" in deps or "@sveltejs/kit" in deps:
        return "svelte"
    if "react-scripts" in deps:
        return "create-react-app"
    if "vite" in deps or "@vitejs/plugin-react" in deps or "@vitejs/plugin-vue" in deps:
        return "vite"
    if "react" in deps and "build" in scripts:
        return "react-build"
    if "vue" in deps and "build" in scripts:
        return "vue-build"
    if "express" in deps:
        return "express"
    if "fastify" in deps:
        return "fastify"
    if "koa" in deps:
        return "koa"
    if "hono" in deps:
        return "hono"
    if "nest" in deps or "@nestjs/core" in deps:
        return "nestjs"
    return None


def has_build_script(repo_path: str) -> bool:
    """Check if package.json has a build script."""
    pkg_json = os.path.join(repo_path, "package.json")
    if not os.path.isfile(pkg_json):
        return False
    try:
        with open(pkg_json, "r", encoding="utf-8") as f:
            pkg = json.load(f)
        return "build" in pkg.get("scripts", {})
    except (OSError, json.JSONDecodeError):
        return False


def has_start_script(repo_path: str) -> bool:
    """Check if package.json has a start script."""
    pkg_json = os.path.join(repo_path, "package.json")
    if not os.path.isfile(pkg_json):
        return False
    try:
        with open(pkg_json, "r", encoding="utf-8") as f:
            pkg = json.load(f)
        return "start" in pkg.get("scripts", {})
    except (OSError, json.JSONDecodeError):
        return False


def has_postinstall_script(repo_path: str) -> bool:
    """Check if package.json has a postinstall script that may need non-package files."""
    pkg_json = os.path.join(repo_path, "package.json")
    if not os.path.isfile(pkg_json):
        return False
    try:
        with open(pkg_json, "r", encoding="utf-8") as f:
            pkg = json.load(f)
        scripts = pkg.get("scripts", {})
        return "postinstall" in scripts or "prepare" in scripts
    except (OSError, json.JSONDecodeError):
        return False


def detect_node_version_from_deps(repo_path: str) -> Optional[str]:
    """Check if any dependency requires a specific Node version higher than default."""
    pkg_json = os.path.join(repo_path, "package.json")
    if not os.path.isfile(pkg_json):
        return None
    try:
        with open(pkg_json, "r", encoding="utf-8") as f:
            content = f.read()
        # Look for node engine requirements in lock file or package.json
        # Many packages specify "engines": { "node": ">=22" } — we need to
        # install first to discover this, so also check package-lock.json
        lock_file = os.path.join(repo_path, "package-lock.json")
        if os.path.isfile(lock_file):
            try:
                with open(lock_file, "r", encoding="utf-8") as f:
                    lock_content = f.read(50000)  # Read first 50KB
                # Find the highest node version requirement
                node_reqs = re.findall(r'"node":\s*">=(\d+)', lock_content)
                if node_reqs:
                    max_ver = max(int(v) for v in node_reqs)
                    if max_ver > 20:
                        return str(max_ver)
            except (OSError, json.JSONDecodeError):
                pass
    except (OSError, json.JSONDecodeError):
        pass
    return None


# ── Python helpers ────────────────────────────────────────────────────────────

def detect_python_framework(repo_path: str) -> Optional[str]:
    """Detect Python web framework from dependencies."""
    deps_content = ""
    for req_file in ("requirements.txt",):
        path = os.path.join(repo_path, req_file)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    deps_content += f.read().lower() + "\n"
            except OSError:
                pass

    pyproject = os.path.join(repo_path, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject, "r", encoding="utf-8", errors="replace") as f:
                deps_content += f.read().lower() + "\n"
        except OSError:
            pass

    if "django" in deps_content and os.path.isfile(os.path.join(repo_path, "manage.py")):
        return "django"
    if "fastapi" in deps_content:
        return "fastapi"
    if "flask" in deps_content:
        return "flask"
    if "streamlit" in deps_content:
        return "streamlit"
    if "gradio" in deps_content:
        return "gradio"
    return None


def detect_python_entry_point(repo_path: str) -> Optional[str]:
    """Detect the main Python file or CLI entry point."""
    # Check pyproject.toml for [project.scripts] or [tool.poetry.scripts]
    pyproject = os.path.join(repo_path, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            # Look for [project.scripts] section
            if re.search(r'\[(?:project\.scripts|tool\.poetry\.scripts)\]', content):
                return "entry_point"  # Signal that pip install . provides CLI
        except OSError:
            pass

    # Common main files
    for candidate in ("app.py", "main.py", "cli.py", "run.py", "server.py"):
        if os.path.isfile(os.path.join(repo_path, candidate)):
            return candidate

    # Scan root-level .py files for if __name__ == "__main__" pattern
    try:
        py_files = [f for f in os.listdir(repo_path)
                    if f.endswith(".py") and os.path.isfile(os.path.join(repo_path, f))]
        for pf in sorted(py_files):
            try:
                with open(os.path.join(repo_path, pf), "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                if re.search(r'if\s+__name__\s*==\s*["\']__main__["\']', content):
                    return pf
            except OSError:
                continue
    except OSError:
        pass

    # If only one .py file exists at root, assume it's the entry point
    try:
        py_files = [f for f in os.listdir(repo_path)
                    if f.endswith(".py") and os.path.isfile(os.path.join(repo_path, f))
                    and f not in ("setup.py", "conftest.py", "__init__.py")]
        if len(py_files) == 1:
            return py_files[0]
    except OSError:
        pass

    return None


# ── Template generators ───────────────────────────────────────────────────────

def generate_template(repo_path: str, detected_stack: str) -> Optional[dict]:
    """Try to generate a Dockerfile from templates. Returns None if no template matches.

    Returns dict with: dockerfile, app_type, app_port, start_command, usage_instructions
    """
    generator = _TEMPLATE_MAP.get(detected_stack)
    if not generator:
        return None

    try:
        result = generator(repo_path)
        if result and result.get("dockerfile"):
            logger.info("Template generated for stack=%s", detected_stack)
            return result
    except Exception as e:
        logger.debug("Template generation failed for %s: %s", detected_stack, e)

    return None


def _template_python_pip(repo_path: str) -> Optional[dict]:
    """Template for Python projects with requirements.txt."""
    py_ver = detect_python_version(repo_path)
    sys_deps = detect_system_deps(repo_path)
    framework = detect_python_framework(repo_path)

    has_req = os.path.isfile(os.path.join(repo_path, "requirements.txt"))
    has_pyproject = os.path.isfile(os.path.join(repo_path, "pyproject.toml"))
    has_setup_py = os.path.isfile(os.path.join(repo_path, "setup.py"))

    lines = [f"FROM python:{py_ver}-slim", "WORKDIR /app", "COPY . /app"]

    # System dependencies
    if sys_deps:
        apt_line = f"RUN apt-get update && apt-get install -y {' '.join(sys_deps)} && rm -rf /var/lib/apt/lists/*"
        lines.append(apt_line)

    # Install Python deps
    if has_req:
        lines.append("RUN pip install --no-cache-dir -r requirements.txt")
        if has_pyproject or has_setup_py:
            lines.append("RUN pip install --no-cache-dir -e .")
    elif has_pyproject:
        lines.append("RUN pip install --no-cache-dir .")
    elif has_setup_py:
        lines.append("RUN pip install --no-cache-dir .")

    # Framework-specific CMD
    if framework == "django":
        lines.append("EXPOSE 8000")
        lines.append('CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]')
        return _make_result(lines, "web", 8000, "python manage.py runserver 0.0.0.0:8000")

    if framework == "fastapi":
        main_mod = _find_main_module(repo_path, "fastapi")
        lines.append("EXPOSE 8000")
        lines.append(f'CMD ["uvicorn", "{main_mod}:app", "--host", "0.0.0.0", "--port", "8000"]')
        return _make_result(lines, "web", 8000, f"uvicorn {main_mod}:app --host 0.0.0.0 --port 8000")

    if framework == "flask":
        lines.append("EXPOSE 5000")
        lines.append('CMD ["flask", "run", "--host=0.0.0.0", "--port=5000"]')
        return _make_result(lines, "web", 5000, "flask run --host=0.0.0.0 --port=5000")

    if framework == "streamlit":
        main_file = "app.py"
        for c in ("app.py", "main.py", "streamlit_app.py"):
            if os.path.isfile(os.path.join(repo_path, c)):
                main_file = c
                break
        lines.append("EXPOSE 8501")
        lines.append(f'CMD ["streamlit", "run", "{main_file}", "--server.port=8501", "--server.address=0.0.0.0"]')
        return _make_result(lines, "web", 8501, f"streamlit run {main_file} --server.port=8501 --server.address=0.0.0.0")

    if framework == "gradio":
        lines.append("EXPOSE 7860")
        lines.append('CMD ["python", "app.py"]')
        return _make_result(lines, "web", 7860, "python app.py")

    # CLI tool — check for entry points
    entry = detect_python_entry_point(repo_path)
    if entry == "entry_point":
        # pyproject.toml defines CLI entry points via pip install
        lines.append('CMD ["tail", "-f", "/dev/null"]')
        return _make_result(lines, "cli", None, None)

    if entry:
        lines.append('CMD ["tail", "-f", "/dev/null"]')
        return _make_result(lines, "cli", None, f"python {entry}")

    # Generic fallback: just install and keep alive
    lines.append('CMD ["tail", "-f", "/dev/null"]')
    return _make_result(lines, "cli", None, None)


def _template_python_poetry(repo_path: str) -> Optional[dict]:
    """Template for Python Poetry projects."""
    py_ver = detect_python_version(repo_path)
    sys_deps = detect_system_deps(repo_path)
    framework = detect_python_framework(repo_path)

    lines = [
        f"FROM python:{py_ver}-slim",
        "WORKDIR /app",
        "COPY . /app",
    ]

    if sys_deps:
        lines.append(f"RUN apt-get update && apt-get install -y {' '.join(sys_deps)} && rm -rf /var/lib/apt/lists/*")

    # Poetry install via pip
    lines.append("RUN pip install --no-cache-dir .")

    if framework == "django":
        lines.append("EXPOSE 8000")
        lines.append('CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]')
        return _make_result(lines, "web", 8000, "python manage.py runserver 0.0.0.0:8000")
    if framework == "fastapi":
        main_mod = _find_main_module(repo_path, "fastapi")
        lines.append("EXPOSE 8000")
        lines.append(f'CMD ["uvicorn", "{main_mod}:app", "--host", "0.0.0.0", "--port", "8000"]')
        return _make_result(lines, "web", 8000, f"uvicorn {main_mod}:app --host 0.0.0.0 --port 8000")
    if framework == "flask":
        lines.append("EXPOSE 5000")
        lines.append('CMD ["flask", "run", "--host=0.0.0.0", "--port=5000"]')
        return _make_result(lines, "web", 5000, "flask run --host=0.0.0.0 --port=5000")

    lines.append('CMD ["tail", "-f", "/dev/null"]')
    return _make_result(lines, "cli", None, None)


def _template_node(repo_path: str) -> Optional[dict]:
    """Template for Node.js projects."""
    node_ver = detect_node_version(repo_path)
    pm = detect_node_package_manager(repo_path)
    framework = detect_node_framework(repo_path)
    build = has_build_script(repo_path)
    start = has_start_script(repo_path)
    has_postinstall = has_postinstall_script(repo_path)

    # Check if any dependency requires a higher Node version than detected
    _dep_node_ver = detect_node_version_from_deps(repo_path)
    if _dep_node_ver:
        dep_major = int(_dep_node_ver.split(".")[0])
        cur_major = int(node_ver.split(".")[0])
        if dep_major > cur_major:
            node_ver = _dep_node_ver

    # Determine install command
    if pm == "pnpm":
        install_cmd = "RUN npm install -g pnpm && pnpm install --frozen-lockfile"
        if not os.path.isfile(os.path.join(repo_path, "pnpm-lock.yaml")):
            install_cmd = "RUN npm install -g pnpm && pnpm install"
    elif pm == "yarn":
        install_cmd = "RUN yarn install --frozen-lockfile"
        if not os.path.isfile(os.path.join(repo_path, "yarn.lock")):
            install_cmd = "RUN yarn install"
    elif pm == "bun":
        install_cmd = "RUN npm install -g bun && bun install"
    else:
        if os.path.isfile(os.path.join(repo_path, "package-lock.json")):
            install_cmd = "RUN npm ci"
        else:
            install_cmd = "RUN npm install"

    def _node_copy_and_install(lines: list[str]):
        """Add COPY + install steps. If postinstall needs extra files, copy everything first."""
        if has_postinstall:
            # postinstall scripts may reference non-package files (e.g., cp .env-example .env)
            # so we must COPY everything BEFORE npm install
            lines.append("COPY . .")
            lines.append(install_cmd)
        else:
            # Standard optimized pattern: copy package files first for Docker layer caching
            if pm == "yarn":
                lines.append("COPY package.json yarn.lock* ./")
            elif pm == "pnpm":
                lines.append("COPY package.json pnpm-lock.yaml* ./")
            else:
                lines.append("COPY package*.json ./")
            lines.append(install_cmd)
            lines.append("COPY . .")

    # SPA frameworks: build then serve static files
    spa_frameworks = ("create-react-app", "vite", "react-build", "vue-build", "angular", "svelte")
    if framework in spa_frameworks and build:
        output_dir = "dist"
        if framework == "create-react-app":
            output_dir = "build"

        lines = [
            f"FROM node:{node_ver}-slim",
            "WORKDIR /app",
        ]
        _node_copy_and_install(lines)
        lines.append(f"RUN {pm} run build")
        lines.append("RUN npm install -g serve")
        lines.append("EXPOSE 3000")
        lines.append(f'CMD ["serve", "-s", "{output_dir}", "-l", "3000"]')
        return _make_result(lines, "web", 3000, f"serve -s {output_dir} -l 3000")

    # Next.js: standalone build
    if framework == "nextjs":
        lines = [
            f"FROM node:{node_ver}-slim",
            "WORKDIR /app",
        ]
        _node_copy_and_install(lines)
        if build:
            lines.append(f"RUN {pm} run build")
        lines.append("EXPOSE 3000")
        lines.append(f'CMD ["{pm}", "start"]')
        return _make_result(lines, "web", 3000, f"{pm} start")

    # Nuxt
    if framework == "nuxt":
        lines = [
            f"FROM node:{node_ver}-slim",
            "WORKDIR /app",
        ]
        _node_copy_and_install(lines)
        if build:
            lines.append(f"RUN {pm} run build")
        lines.append("EXPOSE 3000")
        lines.append(f'CMD ["{pm}", "start"]')
        return _make_result(lines, "web", 3000, f"{pm} start")

    # NestJS
    if framework == "nestjs":
        lines = [
            f"FROM node:{node_ver}-slim",
            "WORKDIR /app",
        ]
        _node_copy_and_install(lines)
        if build:
            lines.append(f"RUN {pm} run build")
        lines.append("EXPOSE 3000")
        if start:
            lines.append(f'CMD ["{pm}", "start"]')
        else:
            lines.append('CMD ["node", "dist/main.js"]')
        return _make_result(lines, "web", 3000, f"{pm} start")

    # Express/Fastify/Koa/Hono: server apps
    if framework in ("express", "fastify", "koa", "hono"):
        port = _detect_node_port(repo_path) or 3000
        main_file = _find_node_main(repo_path)
        lines = [
            f"FROM node:{node_ver}-slim",
            "WORKDIR /app",
        ]
        _node_copy_and_install(lines)
        if build:
            lines.append(f"RUN {pm} run build")
        lines.append(f"EXPOSE {port}")
        if start:
            lines.append(f'CMD ["{pm}", "start"]')
        else:
            lines.append(f'CMD ["node", "{main_file}"]')
        return _make_result(lines, "web", port, f"{pm} start" if start else f"node {main_file}")

    # Generic Node.js with start script
    if start:
        port = _detect_node_port(repo_path) or 3000
        lines = [
            f"FROM node:{node_ver}-slim",
            "WORKDIR /app",
        ]
        _node_copy_and_install(lines)
        if build:
            lines.append(f"RUN {pm} run build")
        lines.append(f"EXPOSE {port}")
        lines.append(f'CMD ["{pm}", "start"]')
        return _make_result(lines, "web", port, f"{pm} start")

    # Node CLI tool (no start script, no framework)
    lines = [
        f"FROM node:{node_ver}-slim",
        "WORKDIR /app",
        "COPY . .",
        install_cmd,
        'CMD ["tail", "-f", "/dev/null"]',
    ]
    return _make_result(lines, "cli", None, None)


def _template_go(repo_path: str) -> Optional[dict]:
    """Template for Go projects."""
    go_ver = detect_go_version(repo_path)

    lines = [
        f"FROM golang:{go_ver}-bookworm",
        "WORKDIR /app",
        "COPY go.mod go.sum* ./",
        "RUN go mod download",
        "COPY . .",
        "RUN go build -o /app/main .",
    ]

    # Detect if it's a web server
    main_go = os.path.join(repo_path, "main.go")
    is_web = False
    port = 8080
    if os.path.isfile(main_go):
        try:
            with open(main_go, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if any(p in content for p in ("http.ListenAndServe", "gin.", "echo.", "fiber.", "chi.", "mux.")):
                is_web = True
                m = re.search(r':(\d{4,5})', content)
                if m:
                    port = int(m.group(1))
        except OSError:
            pass

    if is_web:
        lines.append(f"EXPOSE {port}")
        lines.append('CMD ["/app/main"]')
        return _make_result(lines, "web", port, "/app/main")
    else:
        lines.append('CMD ["tail", "-f", "/dev/null"]')
        return _make_result(lines, "cli", None, "/app/main")


def _template_rust(repo_path: str) -> Optional[dict]:
    """Template for Rust projects."""
    lines = [
        "FROM rust:1.77-slim",
        "WORKDIR /app",
        "COPY . .",
        "RUN cargo build --release",
    ]

    # Try to find binary name from Cargo.toml
    bin_name = "app"
    cargo_toml = os.path.join(repo_path, "Cargo.toml")
    if os.path.isfile(cargo_toml):
        try:
            with open(cargo_toml, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            m = re.search(r'name\s*=\s*"([^"]+)"', content)
            if m:
                bin_name = m.group(1)
        except OSError:
            pass

    lines.append(f'CMD ["./target/release/{bin_name}"]')
    return _make_result(lines, "cli", None, f"./target/release/{bin_name}")


def _template_ruby(repo_path: str) -> Optional[dict]:
    """Template for Ruby projects."""
    lines = [
        "FROM ruby:3.3-slim",
        "WORKDIR /app",
        "COPY . .",
        "RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*",
        "RUN bundle install",
    ]

    # Rails?
    if os.path.isfile(os.path.join(repo_path, "config", "routes.rb")):
        lines.append("EXPOSE 3000")
        lines.append('CMD ["rails", "server", "-b", "0.0.0.0"]')
        return _make_result(lines, "web", 3000, "rails server -b 0.0.0.0")

    lines.append('CMD ["tail", "-f", "/dev/null"]')
    return _make_result(lines, "cli", None, None)


def _template_java_gradle(repo_path: str) -> Optional[dict]:
    """Template for Java Gradle projects."""
    lines = [
        "FROM eclipse-temurin:21-jdk",
        "WORKDIR /app",
        "COPY . .",
        "RUN chmod +x gradlew 2>/dev/null; ./gradlew build -x test || gradle build -x test",
    ]

    # Spring Boot?
    is_spring = False
    for gf in ("build.gradle", "build.gradle.kts"):
        path = os.path.join(repo_path, gf)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    if "spring" in f.read().lower():
                        is_spring = True
            except OSError:
                pass

    if is_spring:
        lines.append("EXPOSE 8080")
        lines.append('CMD ["java", "-jar", "build/libs/*.jar"]')
        return _make_result(lines, "web", 8080, "java -jar build/libs/*.jar")

    lines.append('CMD ["tail", "-f", "/dev/null"]')
    return _make_result(lines, "cli", None, None)


def _template_java_maven(repo_path: str) -> Optional[dict]:
    """Template for Java Maven projects."""
    lines = [
        "FROM eclipse-temurin:21-jdk",
        "WORKDIR /app",
        "COPY . .",
        "RUN chmod +x mvnw 2>/dev/null; ./mvnw package -DskipTests || mvn package -DskipTests",
    ]

    # Spring Boot?
    pom = os.path.join(repo_path, "pom.xml")
    is_spring = False
    if os.path.isfile(pom):
        try:
            with open(pom, "r", encoding="utf-8", errors="replace") as f:
                if "spring" in f.read().lower():
                    is_spring = True
        except OSError:
            pass

    if is_spring:
        lines.append("EXPOSE 8080")
        lines.append('CMD ["java", "-jar", "target/*.jar"]')
        return _make_result(lines, "web", 8080, "java -jar target/*.jar")

    lines.append('CMD ["tail", "-f", "/dev/null"]')
    return _make_result(lines, "cli", None, None)


def _template_php(repo_path: str) -> Optional[dict]:
    """Template for PHP projects (Laravel/generic)."""
    lines = [
        "FROM php:8.3-cli",
        "WORKDIR /app",
        "COPY . .",
    ]

    # Laravel?
    if os.path.isfile(os.path.join(repo_path, "artisan")):
        lines = [
            "FROM php:8.3-cli",
            "WORKDIR /app",
            "RUN apt-get update && apt-get install -y unzip git && rm -rf /var/lib/apt/lists/*",
            "COPY --from=composer:latest /usr/bin/composer /usr/bin/composer",
            "COPY . .",
            "RUN composer install --no-dev --optimize-autoloader",
            "EXPOSE 8000",
            'CMD ["php", "artisan", "serve", "--host=0.0.0.0", "--port=8000"]',
        ]
        return _make_result(lines, "web", 8000, "php artisan serve --host=0.0.0.0 --port=8000")

    # Generic PHP with composer
    if os.path.isfile(os.path.join(repo_path, "composer.json")):
        lines = [
            "FROM php:8.3-cli",
            "WORKDIR /app",
            "RUN apt-get update && apt-get install -y unzip && rm -rf /var/lib/apt/lists/*",
            "COPY --from=composer:latest /usr/bin/composer /usr/bin/composer",
            "COPY . .",
            "RUN composer install",
            'CMD ["tail", "-f", "/dev/null"]',
        ]
        return _make_result(lines, "cli", None, None)

    lines.append('CMD ["tail", "-f", "/dev/null"]')
    return _make_result(lines, "cli", None, None)


def _template_dotnet(repo_path: str) -> Optional[dict]:
    """Template for .NET projects."""
    lines = [
        "FROM mcr.microsoft.com/dotnet/sdk:8.0",
        "WORKDIR /app",
        "COPY . .",
        "RUN dotnet restore",
        "RUN dotnet publish -c Release -o out",
        "EXPOSE 8080",
        'CMD ["dotnet", "out/*.dll"]',
    ]
    return _make_result(lines, "web", 8080, "dotnet out/*.dll")


def _template_elixir(repo_path: str) -> Optional[dict]:
    """Template for Elixir projects."""
    lines = [
        "FROM elixir:1.16-slim",
        "WORKDIR /app",
        "COPY . .",
        "RUN mix local.hex --force && mix local.rebar --force",
        "RUN mix deps.get",
        "RUN mix compile",
    ]

    # Phoenix?
    mix_exs = os.path.join(repo_path, "mix.exs")
    if os.path.isfile(mix_exs):
        try:
            with open(mix_exs, "r", encoding="utf-8", errors="replace") as f:
                if "phoenix" in f.read().lower():
                    lines.append("EXPOSE 4000")
                    lines.append('CMD ["mix", "phx.server"]')
                    return _make_result(lines, "web", 4000, "mix phx.server")
        except OSError:
            pass

    lines.append('CMD ["tail", "-f", "/dev/null"]')
    return _make_result(lines, "cli", None, None)


def _template_static_site(repo_path: str) -> Optional[dict]:
    """Template for static HTML sites (index.html in root)."""
    lines = [
        "FROM nginx:alpine",
        "COPY . /usr/share/nginx/html",
        "EXPOSE 80",
        'CMD ["nginx", "-g", "daemon off;"]',
    ]
    return _make_result(lines, "web", 80, "nginx -g 'daemon off;'")


def _template_generic(repo_path: str) -> Optional[dict]:
    """Fallback template — only used when we detect specific patterns."""
    # Check for index.html (static site)
    if os.path.isfile(os.path.join(repo_path, "index.html")):
        return _template_static_site(repo_path)

    # Check for Makefile-based C/C++ projects
    if os.path.isfile(os.path.join(repo_path, "Makefile")) or os.path.isfile(os.path.join(repo_path, "CMakeLists.txt")):
        lines = [
            "FROM ubuntu:24.04",
            "WORKDIR /app",
            "RUN apt-get update && apt-get install -y build-essential cmake && rm -rf /var/lib/apt/lists/*",
            "COPY . .",
        ]
        if os.path.isfile(os.path.join(repo_path, "CMakeLists.txt")):
            lines.append("RUN mkdir -p build && cd build && cmake .. && make")
        else:
            lines.append("RUN make")
        lines.append('CMD ["tail", "-f", "/dev/null"]')
        return _make_result(lines, "cli", None, None)

    # Check for shell scripts
    for sh in ("run.sh", "start.sh", "main.sh", "install.sh"):
        if os.path.isfile(os.path.join(repo_path, sh)):
            lines = [
                "FROM ubuntu:24.04",
                "WORKDIR /app",
                "RUN apt-get update && apt-get install -y curl wget git && rm -rf /var/lib/apt/lists/*",
                "COPY . .",
                f"RUN chmod +x {sh}",
                'CMD ["tail", "-f", "/dev/null"]',
            ]
            return _make_result(lines, "cli", None, f"./{sh}")

    return None  # Can't template this — let AI handle it


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_result(lines: list[str], app_type: str, app_port: Optional[int],
                 start_command: Optional[str], usage_instructions: str = "") -> dict:
    # Auto-generate basic usage if not provided
    if not usage_instructions and start_command:
        if app_type == "cli":
            usage_instructions = f"CLI Application — use the Terminal tab\n\nRun:\n    {start_command}"
        elif app_type == "web":
            usage_instructions = f"Web application running on port {app_port or 'auto-detected'}"
    return {
        "dockerfile": "\n".join(lines),
        "app_type": app_type,
        "app_port": app_port,
        "start_command": start_command,
        "usage_instructions": usage_instructions,
        "source": "template",
    }


def _find_main_module(repo_path: str, framework: str) -> str:
    """Find the Python module containing the app."""
    for candidate in ("main.py", "app.py", "server.py", "api.py", "run.py"):
        path = os.path.join(repo_path, candidate)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    if framework.lower() in f.read().lower():
                        return candidate.replace(".py", "")
            except OSError:
                continue
    return "main"


def _detect_node_port(repo_path: str) -> Optional[int]:
    """Detect the port a Node.js app will listen on.

    Checks (in order): .env.example/.env.sample for PORT=, package.json start
    script for --port/PORT= flags, and app.js/server.js for .listen(PORT) calls.
    Returns None if no port is found (caller should default to 3000).
    """
    # 1. Check .env files
    for env_name in (".env.example", ".env.sample", ".env.development", ".env"):
        env_path = os.path.join(repo_path, env_name)
        if not os.path.isfile(env_path):
            continue
        try:
            with open(env_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = re.match(r'^PORT\s*=\s*(\d+)', line.strip())
                    if m:
                        return int(m.group(1))
        except OSError:
            continue

    # 2. Check package.json start script for port hints
    pkg_path = os.path.join(repo_path, "package.json")
    if os.path.isfile(pkg_path):
        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            start_script = pkg.get("scripts", {}).get("start", "")
            m = re.search(r'(?:--port|PORT=|-p)\s*(\d+)', start_script)
            if m:
                return int(m.group(1))
        except (OSError, json.JSONDecodeError):
            pass

    # 3. Check main source files for .listen(PORT) calls
    for src in ("app.js", "server.js", "index.js", "src/app.js", "src/index.js"):
        src_path = os.path.join(repo_path, src)
        if not os.path.isfile(src_path):
            continue
        try:
            with open(src_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(5000)
            m = re.search(r'\.listen\(\s*(\d{4,5})', content)
            if m:
                return int(m.group(1))
        except OSError:
            continue

    return None


def _find_node_main(repo_path: str) -> str:
    """Find the main Node.js entry file."""
    pkg_json = os.path.join(repo_path, "package.json")
    if os.path.isfile(pkg_json):
        try:
            with open(pkg_json, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            main = pkg.get("main", "")
            if main:
                return main
        except (OSError, json.JSONDecodeError):
            pass

    for candidate in ("index.js", "server.js", "app.js", "main.js",
                       "src/index.js", "src/server.js", "src/app.js",
                       "index.ts", "server.ts", "app.ts",
                       "src/index.ts", "src/server.ts"):
        if os.path.isfile(os.path.join(repo_path, candidate)):
            return candidate
    return "index.js"


def _template_deno(repo_path: str) -> Optional[dict]:
    """Template for Deno projects."""
    # Find main file
    main_file = "main.ts"
    for candidate in ("main.ts", "mod.ts", "main.js", "app.ts", "server.ts", "src/main.ts"):
        if os.path.isfile(os.path.join(repo_path, candidate)):
            main_file = candidate
            break

    lines = [
        "FROM denoland/deno:latest",
        "WORKDIR /app",
        "COPY . .",
    ]

    # Check if it's a web server
    is_web = False
    try:
        with open(os.path.join(repo_path, main_file), "r", encoding="utf-8", errors="replace") as f:
            content = f.read(5000)
        if any(p in content for p in ("Deno.serve", "serve(", "listenAndServe", "oak", "hono")):
            is_web = True
    except OSError:
        pass

    if is_web:
        lines.append("EXPOSE 8000")
        lines.append(f'CMD ["deno", "run", "--allow-all", "{main_file}"]')
        return _make_result(lines, "web", 8000, f"deno run --allow-all {main_file}")

    lines.append('CMD ["tail", "-f", "/dev/null"]')
    return _make_result(lines, "cli", None, f"deno run --allow-all {main_file}")


def _template_bun(repo_path: str) -> Optional[dict]:
    """Template for Bun projects."""
    node_ver = detect_node_version(repo_path)
    framework = detect_node_framework(repo_path)
    build = has_build_script(repo_path)
    start = has_start_script(repo_path)

    lines = [
        "FROM oven/bun:latest",
        "WORKDIR /app",
        "COPY . .",
        "RUN bun install",
    ]

    if build:
        lines.append("RUN bun run build")

    if start:
        lines.append("EXPOSE 3000")
        lines.append('CMD ["bun", "start"]')
        return _make_result(lines, "web", 3000, "bun start")

    main_file = _find_node_main(repo_path)
    lines.append('CMD ["tail", "-f", "/dev/null"]')
    return _make_result(lines, "cli", None, f"bun run {main_file}")


def _template_c_cpp(repo_path: str) -> Optional[dict]:
    """Template for C/C++ projects."""
    lines = [
        "FROM ubuntu:24.04",
        "WORKDIR /app",
        "RUN apt-get update && apt-get install -y build-essential cmake pkg-config && rm -rf /var/lib/apt/lists/*",
        "COPY . .",
    ]

    if os.path.isfile(os.path.join(repo_path, "CMakeLists.txt")):
        lines.append("RUN mkdir -p build && cd build && cmake .. && make")
    elif os.path.isfile(os.path.join(repo_path, "Makefile")):
        lines.append("RUN make")
    elif os.path.isfile(os.path.join(repo_path, "configure")):
        lines.append("RUN ./configure && make")
    else:
        # Try to compile all .c files
        lines.append('RUN gcc -o main *.c 2>/dev/null || g++ -o main *.cpp 2>/dev/null || true')

    lines.append('CMD ["tail", "-f", "/dev/null"]')
    return _make_result(lines, "cli", None, None)


def _template_scala(repo_path: str) -> Optional[dict]:
    """Template for Scala SBT projects."""
    lines = [
        "FROM eclipse-temurin:21-jdk",
        "WORKDIR /app",
        "RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*",
        'RUN curl -fL "https://github.com/sbt/sbt/releases/download/v1.9.8/sbt-1.9.8.tgz" | tar xz -C /usr/local',
        "ENV PATH=\"/usr/local/sbt/bin:${PATH}\"",
        "COPY . .",
        "RUN sbt compile",
        'CMD ["tail", "-f", "/dev/null"]',
    ]
    return _make_result(lines, "cli", None, None)


# ── Stack → template mapping ─────────────────────────────────────────────────

_TEMPLATE_MAP: dict[str, callable] = {
    "python-pip": _template_python_pip,
    "python-poetry": _template_python_poetry,
    "python-conda": _template_python_pip,  # conda uses same base with pip fallback
    "node": _template_node,
    "go": _template_go,
    "rust": _template_rust,
    "ruby": _template_ruby,
    "java-gradle": _template_java_gradle,
    "java-maven": _template_java_maven,
    "php": _template_php,
    "dotnet": _template_dotnet,
    "elixir": _template_elixir,
    "deno": _template_deno,
    "bun": _template_bun,
    "c-cpp": _template_c_cpp,
    "scala": _template_scala,
    "static-site": _template_static_site,
    "generic": _template_generic,
}
