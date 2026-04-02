"""Repository cloning, README parsing, and config file analysis."""

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from analyzer.stack_detector import StackDetector


@dataclass
class AnalysisResult:
    detected_stack: str = "generic"
    install_commands: list[str] = field(default_factory=list)
    install_source: Optional[str] = None
    ai_confidence: Optional[float] = None
    deploy_method: str = "sandbox"  # "docker-run", "docker-compose", "dockerfile", "sandbox"
    docker_image: Optional[str] = None  # only for deploy_method="docker-run"


class RepoAnalyzer:
    """Analyzes a cloned repository to determine install commands."""

    # Patterns that indicate an install/setup section in README
    SECTION_PATTERNS = re.compile(
        r"^#{1,3}\s*(install|setup|getting\s+started|quick\s*start|usage|build|development)",
        re.IGNORECASE | re.MULTILINE,
    )

    # Shell command prefixes we recognize
    COMMAND_PREFIXES = (
        "npm", "yarn", "pnpm", "npx",
        "pip", "pip3", "poetry", "pipenv", "conda",
        "cargo", "rustup",
        "go ", "go build", "go mod", "go run", "go install",
        "make", "cmake",
        "docker", "docker-compose",
        "apt", "brew",
        "./install", "./setup", "./configure",
        "bundle", "gem",
        "composer",
        "dotnet", "nuget",
        "mix",
        "mvn", "gradle", "./gradlew",
        "python", "python3",
    )

    def analyze(self, repo_path: str) -> AnalysisResult:
        """Run the full analysis pipeline.

        Order: docker-compose → dockerfile → config files → README → templates → empty (caller invokes AI).
        """
        stack = StackDetector.detect(repo_path)

        # Step 0: Docker-native deployment takes priority
        docker_result = self._detect_docker_deploy(repo_path, stack)
        if docker_result:
            return docker_result

        # Step 1: Try config files first (more reliable than README)
        commands = self._parse_config_files(repo_path, stack)
        if commands:
            return AnalysisResult(
                detected_stack=stack,
                install_commands=commands,
                install_source="config_file",
            )

        # Step 2: Try README
        commands = self._parse_readme(repo_path)
        if commands:
            return AnalysisResult(
                detected_stack=stack,
                install_commands=commands,
                install_source="readme",
            )

        # Step 3: Try templates (DB lookup)
        commands = self._lookup_template(stack)
        if commands:
            return AnalysisResult(
                detected_stack=stack,
                install_commands=commands,
                install_source="template",
            )

        # Step 4: Return empty — caller will invoke AI
        return AnalysisResult(detected_stack=stack)

    def _detect_docker_deploy(self, repo_path: str, stack: str) -> Optional["AnalysisResult"]:
        """Check if repo ships docker-compose.yml or Dockerfile for native Docker deployment."""
        for filename in ["docker-compose.yml", "docker-compose.yaml"]:
            if os.path.isfile(os.path.join(repo_path, filename)):
                return AnalysisResult(
                    detected_stack=stack,
                    install_commands=[],
                    install_source="docker-compose",
                    deploy_method="docker-compose",
                )

        if os.path.isfile(os.path.join(repo_path, "Dockerfile")):
            return AnalysisResult(
                detected_stack=stack,
                install_commands=[],
                install_source="dockerfile",
                deploy_method="dockerfile",
            )

        return None

    def _parse_readme(self, repo_path: str) -> list[str]:
        """Extract install commands from README files."""
        readme_path = self._find_readme(repo_path)
        if not readme_path:
            return []

        try:
            with open(readme_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            return []

        # Find install/setup sections
        sections = list(self.SECTION_PATTERNS.finditer(content))
        if not sections:
            # No labeled sections — try to extract code blocks from entire file
            return self._extract_commands_from_text(content)

        commands: list[str] = []
        for i, match in enumerate(sections):
            start = match.start()
            # Section ends at the next section or EOF
            end = sections[i + 1].start() if i + 1 < len(sections) else len(content)
            section_text = content[start:end]
            commands.extend(self._extract_commands_from_text(section_text))

        return commands

    def _extract_commands_from_text(self, text: str) -> list[str]:
        """Extract shell commands from fenced code blocks."""
        commands: list[str] = []

        # Match fenced code blocks (``` or ~~~)
        code_block_pattern = re.compile(
            r"(?:```|~~~)(?:bash|sh|shell|console|zsh|terminal)?\s*\n(.*?)(?:```|~~~)",
            re.DOTALL,
        )

        for block_match in code_block_pattern.finditer(text):
            block = block_match.group(1)
            for line in block.strip().splitlines():
                line = line.strip()
                # Remove shell prompt indicators
                line = re.sub(r"^\$\s+", "", line)
                line = re.sub(r"^>\s+", "", line)

                if not line or line.startswith("#"):
                    continue

                if self._is_install_command(line):
                    commands.append(line)

        return commands

    def _is_install_command(self, line: str) -> bool:
        """Check if a line looks like a valid install/build command."""
        for prefix in self.COMMAND_PREFIXES:
            if line.startswith(prefix):
                return True
        return False

    def _find_readme(self, repo_path: str) -> Optional[str]:
        """Find README file case-insensitively."""
        candidates = ["README.md", "README.rst", "README.txt", "README", "Readme.md", "readme.md"]
        for name in candidates:
            path = os.path.join(repo_path, name)
            if os.path.isfile(path):
                return path

        # Case-insensitive fallback
        try:
            for entry in os.listdir(repo_path):
                if entry.lower().startswith("readme"):
                    full = os.path.join(repo_path, entry)
                    if os.path.isfile(full):
                        return full
        except OSError:
            pass
        return None

    def _parse_config_files(self, repo_path: str, stack: str) -> list[str]:
        """Derive install commands from known config files."""
        commands: list[str] = []

        if stack == "node" or os.path.isfile(os.path.join(repo_path, "package.json")):
            commands = self._parse_node(repo_path)
        elif stack == "python-poetry":
            commands = ["poetry install"]
        elif stack == "python-pip" or os.path.isfile(os.path.join(repo_path, "requirements.txt")):
            if os.path.isfile(os.path.join(repo_path, "setup.py")):
                commands = ["pip install -e ."]
            else:
                commands = ["pip install -r requirements.txt"]
        elif stack == "python-conda":
            commands = ["conda env create -f environment.yml"]
        elif stack == "rust" or os.path.isfile(os.path.join(repo_path, "Cargo.toml")):
            commands = ["cargo build --release"]
        elif stack == "go" or os.path.isfile(os.path.join(repo_path, "go.mod")):
            commands = ["go mod download", "go build ./..."]
        elif stack == "java-maven" or os.path.isfile(os.path.join(repo_path, "pom.xml")):
            commands = ["mvn install -DskipTests"]
        elif stack == "java-gradle":
            if os.path.isfile(os.path.join(repo_path, "gradlew")):
                commands = ["./gradlew build"]
            else:
                commands = ["gradle build"]
        elif stack == "ruby" or os.path.isfile(os.path.join(repo_path, "Gemfile")):
            commands = ["bundle install"]
        elif stack == "php" or os.path.isfile(os.path.join(repo_path, "composer.json")):
            commands = ["composer install"]
        elif stack == "elixir" or os.path.isfile(os.path.join(repo_path, "mix.exs")):
            commands = ["mix deps.get", "mix compile"]
        elif os.path.isfile(os.path.join(repo_path, "Makefile")):
            commands = self._parse_makefile(repo_path)

        return commands

    def _parse_node(self, repo_path: str) -> list[str]:
        """Parse package.json for install and build scripts."""
        import json

        pkg_path = os.path.join(repo_path, "package.json")
        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                pkg = json.load(f)
        except (OSError, json.JSONDecodeError):
            return ["npm install"]

        # Detect package manager
        pm = "npm"
        if os.path.isfile(os.path.join(repo_path, "yarn.lock")):
            pm = "yarn"
        elif os.path.isfile(os.path.join(repo_path, "pnpm-lock.yaml")):
            pm = "pnpm"

        commands = [f"{pm} install"]

        scripts = pkg.get("scripts", {})
        if "build" in scripts:
            commands.append(f"{pm} run build")
        if "start" in scripts and "build" not in scripts:
            commands.append(f"{pm} start")

        return commands

    def _parse_makefile(self, repo_path: str) -> list[str]:
        """Check Makefile for install or build targets."""
        makefile_path = os.path.join(repo_path, "Makefile")
        try:
            with open(makefile_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            return []

        targets = re.findall(r"^(\w+)\s*:", content, re.MULTILINE)
        if "install" in targets:
            return ["make install"]
        if "build" in targets:
            return ["make build"]
        if "all" in targets:
            return ["make all"]
        return []

    def _lookup_template(self, stack: str) -> list[str]:
        """Query install_templates table for a known stack."""
        try:
            from db.database import get_sync_session
            from db.models import InstallTemplate
            from sqlalchemy import select

            with get_sync_session() as session:
                stmt = (
                    select(InstallTemplate)
                    .where(InstallTemplate.stack == stack)
                    .order_by(InstallTemplate.confidence.desc())
                    .limit(1)
                )
                template = session.execute(stmt).scalar_one_or_none()
                if template:
                    return template.commands
        except Exception:
            pass
        return []
