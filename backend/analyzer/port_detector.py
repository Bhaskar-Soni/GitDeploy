"""Detect which port an application listens on and how to start it."""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AppRunInfo:
    """Information about how to run the app and what port it listens on."""
    app_type: str = "cli"  # "web" or "cli"
    start_command: Optional[str] = None
    port: int = 3000  # Default fallback
    detected_from: str = "default"
    env_overrides: dict[str, str] = field(default_factory=dict)


# Common framework default ports
FRAMEWORK_PORTS = {
    "react": 3000,
    "next": 3000,
    "vue": 5173,
    "nuxt": 3000,
    "angular": 4200,
    "svelte": 5173,
    "vite": 5173,
    "express": 3000,
    "fastapi": 8000,
    "flask": 5000,
    "django": 8000,
    "rails": 3000,
    "spring": 8080,
    "gin": 8080,
    "actix": 8080,
    "rocket": 8000,
    "phoenix": 4000,
    "laravel": 8000,
    "grafana": 3000,
    "strapi": 1337,
    "ghost": 2368,
    "wordpress": 80,
}


class PortDetector:
    """Detects the port an app will listen on and the start command."""

    def detect(self, repo_path: str, stack: str) -> AppRunInfo:
        """Analyze the repo and determine port + start command.

        Checks in order:
        1. Dockerfile EXPOSE directives
        2. docker-compose.yml port mappings
        3. package.json scripts (for Node)
        4. Framework-specific config files
        5. Source code port bindings
        6. .env files for PORT variable
        7. Known framework defaults
        """
        info = AppRunInfo()

        # Check Dockerfile
        dockerfile_info = self._check_dockerfile(repo_path)
        if dockerfile_info:
            info.port = dockerfile_info.port
            info.app_type = "web"
            info.detected_from = "Dockerfile"
            if dockerfile_info.start_command:
                info.start_command = dockerfile_info.start_command

        # Check docker-compose for port mappings
        compose_info = self._check_docker_compose(repo_path)
        if compose_info:
            info.port = compose_info.port
            info.app_type = "web"
            info.detected_from = "docker-compose"

        # Check package.json for Node apps
        if stack in ("node",) or os.path.isfile(os.path.join(repo_path, "package.json")):
            node_info = self._check_package_json(repo_path)
            if node_info:
                if node_info.start_command:
                    info.start_command = node_info.start_command
                if node_info.port != 3000 or info.detected_from == "default":
                    info.port = node_info.port
                info.app_type = "web"
                info.detected_from = node_info.detected_from

        # Check Python frameworks
        if stack in ("python-pip", "python-poetry", "python-conda"):
            py_info = self._check_python(repo_path)
            if py_info:
                info.start_command = py_info.start_command
                info.port = py_info.port
                info.app_type = "web"
                info.detected_from = py_info.detected_from

        # Check Go
        if stack == "go":
            go_info = self._check_go(repo_path)
            if go_info:
                info.port = go_info.port
                info.app_type = "web"
                info.detected_from = go_info.detected_from
                info.start_command = go_info.start_command

        # Check .env for PORT
        env_port = self._check_env_files(repo_path)
        if env_port:
            info.port = env_port
            info.detected_from = ".env"

        # Check source files for port bindings
        source_port = self._scan_source_for_port(repo_path)
        if source_port and info.detected_from == "default":
            info.port = source_port
            info.app_type = "web"
            info.detected_from = "source_code"

        # Fallback: if we have a start command, it's a web app
        if info.start_command and info.app_type == "cli":
            info.app_type = "web"

        # Force the app to listen on 0.0.0.0 so Docker can expose it
        info.env_overrides["HOST"] = "0.0.0.0"
        info.env_overrides["HOSTNAME"] = "0.0.0.0"
        info.env_overrides["PORT"] = str(info.port)

        return info

    def _check_dockerfile(self, repo_path: str) -> Optional[AppRunInfo]:
        """Check Dockerfile for EXPOSE and CMD/ENTRYPOINT."""
        for name in ("Dockerfile", "dockerfile"):
            path = os.path.join(repo_path, name)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue

            info = AppRunInfo()

            # Find EXPOSE
            expose = re.findall(r"^EXPOSE\s+(\d+)", content, re.MULTILINE)
            if expose:
                info.port = int(expose[0])

            # Find CMD
            cmd_match = re.search(r'^CMD\s+\[(.+)\]', content, re.MULTILINE)
            if cmd_match:
                try:
                    parts = json.loads(f"[{cmd_match.group(1)}]")
                    info.start_command = " ".join(parts)
                except (json.JSONDecodeError, TypeError):
                    pass
            else:
                cmd_match = re.search(r'^CMD\s+(.+)$', content, re.MULTILINE)
                if cmd_match:
                    info.start_command = cmd_match.group(1).strip()

            if expose:
                return info
        return None

    def _check_docker_compose(self, repo_path: str) -> Optional[AppRunInfo]:
        """Check docker-compose.yml for port mappings."""
        for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            path = os.path.join(repo_path, name)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue

            # Find port mappings like "3000:3000" or "8080:80"
            ports = re.findall(r'["\']?(\d+):(\d+)["\']?', content)
            if ports:
                # Take the first non-database port
                db_ports = {5432, 3306, 27017, 6379, 11434}
                for host_port, container_port in ports:
                    if int(container_port) not in db_ports:
                        return AppRunInfo(port=int(container_port))
        return None

    def _check_package_json(self, repo_path: str) -> Optional[AppRunInfo]:
        """Check package.json for start scripts and framework hints."""
        pkg_path = os.path.join(repo_path, "package.json")
        if not os.path.isfile(pkg_path):
            return None

        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                pkg = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

        info = AppRunInfo(detected_from="package.json")
        scripts = pkg.get("scripts", {})
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

        # Detect package manager
        pm = "npm"
        if os.path.isfile(os.path.join(repo_path, "yarn.lock")):
            pm = "yarn"
        elif os.path.isfile(os.path.join(repo_path, "pnpm-lock.yaml")):
            pm = "pnpm"

        # Determine start command
        if "start" in scripts:
            info.start_command = f"{pm} start"
        elif "dev" in scripts:
            info.start_command = f"{pm} run dev"
        elif "serve" in scripts:
            info.start_command = f"{pm} run serve"
        elif "preview" in scripts:
            info.start_command = f"{pm} run preview"

        # Detect port from scripts
        start_script = scripts.get("start", "") + scripts.get("dev", "")
        port_match = re.search(r'(?:--port|PORT=|-p)\s*(\d+)', start_script)
        if port_match:
            info.port = int(port_match.group(1))
            return info

        # Detect framework and use default port
        for framework, default_port in FRAMEWORK_PORTS.items():
            if framework in deps:
                info.port = default_port
                return info

        # Check for Next.js
        if "next" in deps:
            info.port = 3000
            if "dev" in scripts:
                info.start_command = f"{pm} run dev"
            return info

        return info if info.start_command else None

    def _check_python(self, repo_path: str) -> Optional[AppRunInfo]:
        """Check for Python web frameworks."""
        # Check for Django
        manage_py = os.path.join(repo_path, "manage.py")
        if os.path.isfile(manage_py):
            return AppRunInfo(
                start_command="python manage.py runserver 0.0.0.0:8000",
                port=8000,
                detected_from="Django manage.py",
            )

        # Check for Flask/FastAPI in requirements
        for req_file in ("requirements.txt", "pyproject.toml"):
            req_path = os.path.join(repo_path, req_file)
            if not os.path.isfile(req_path):
                continue
            try:
                with open(req_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read().lower()
            except OSError:
                continue

            if "fastapi" in content or "uvicorn" in content:
                # Find the main module
                main_module = self._find_python_main(repo_path, "fastapi")
                cmd = f"uvicorn {main_module}:app --host 0.0.0.0 --port 8000" if main_module else "uvicorn main:app --host 0.0.0.0 --port 8000"
                return AppRunInfo(start_command=cmd, port=8000, detected_from="FastAPI")

            if "flask" in content:
                main_module = self._find_python_main(repo_path, "flask")
                cmd = f"flask run --host=0.0.0.0 --port=5000"
                return AppRunInfo(start_command=cmd, port=5000, detected_from="Flask")

            if "streamlit" in content:
                # Find main streamlit file
                for candidate in ("app.py", "main.py", "streamlit_app.py"):
                    if os.path.isfile(os.path.join(repo_path, candidate)):
                        return AppRunInfo(
                            start_command=f"streamlit run {candidate} --server.port=8501 --server.address=0.0.0.0",
                            port=8501,
                            detected_from="Streamlit",
                        )

            if "gradio" in content:
                return AppRunInfo(
                    start_command="python app.py",
                    port=7860,
                    detected_from="Gradio",
                )

        return None

    def _find_python_main(self, repo_path: str, framework: str) -> Optional[str]:
        """Find the Python module that creates the app."""
        for candidate in ("main.py", "app.py", "server.py", "api.py", "run.py"):
            path = os.path.join(repo_path, candidate)
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    if framework.lower() in content.lower():
                        return candidate.replace(".py", "")
                except OSError:
                    continue
        return None

    def _check_go(self, repo_path: str) -> Optional[AppRunInfo]:
        """Check Go projects for HTTP server patterns."""
        main_path = os.path.join(repo_path, "main.go")
        if not os.path.isfile(main_path):
            # Look in cmd/ directory
            cmd_dir = os.path.join(repo_path, "cmd")
            if os.path.isdir(cmd_dir):
                return AppRunInfo(
                    start_command="go run ./cmd/...",
                    port=8080,
                    detected_from="Go cmd/",
                )
            return None

        try:
            with open(main_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            return None

        if "http.ListenAndServe" in content or "gin" in content or "echo" in content or "fiber" in content:
            port_match = re.search(r':(\d{4,5})', content)
            port = int(port_match.group(1)) if port_match else 8080
            return AppRunInfo(
                start_command="go run .",
                port=port,
                detected_from="Go HTTP server",
            )
        return None

    def _check_env_files(self, repo_path: str) -> Optional[int]:
        """Check .env files for PORT variable."""
        for name in (".env.example", ".env.sample", ".env.development", ".env"):
            path = os.path.join(repo_path, name)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        match = re.match(r'^PORT\s*=\s*(\d+)', line.strip())
                        if match:
                            return int(match.group(1))
            except OSError:
                continue
        return None

    def _scan_source_for_port(self, repo_path: str) -> Optional[int]:
        """Scan source files for common port binding patterns."""
        patterns = [
            r'\.listen\(\s*(\d{4,5})',           # .listen(3000)
            r'port\s*[=:]\s*(\d{4,5})',           # port = 3000 or port: 3000
            r'PORT\s*[=:]\s*["\']?(\d{4,5})',     # PORT=3000
            r'--port\s+(\d{4,5})',                 # --port 8080
            r'addr\s*=\s*["\']:(\d{4,5})',        # addr = ":8080"
        ]
        compiled = [re.compile(p, re.IGNORECASE) for p in patterns]

        source_exts = {".js", ".ts", ".py", ".go", ".rb", ".java", ".rs"}
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv", "vendor", "target"}]
            depth = os.path.relpath(root, repo_path).count(os.sep)
            if depth > 2:
                dirs.clear()
                continue

            for fn in files:
                if os.path.splitext(fn)[1] not in source_exts:
                    continue
                try:
                    with open(os.path.join(root, fn), "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(10_000)
                except OSError:
                    continue

                for pat in compiled:
                    m = pat.search(content)
                    if m:
                        port = int(m.group(1))
                        if 1024 < port < 65535:
                            return port
        return None
