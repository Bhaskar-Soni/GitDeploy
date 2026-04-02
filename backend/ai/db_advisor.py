"""AI-powered database requirement advisor.

Called when the static DBDetector confidence is between 0.5 and 0.7 (ambiguous).
Uses the configured AI provider via AIClient.
"""

import json
import os
from typing import Optional

from analyzer.db_detector import DBRequirement
from ai.ai_client import AIClient

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "target", "dist", "build"}

KEY_FILES = [
    "README.md", "package.json", "pyproject.toml", "requirements.txt",
    "docker-compose.yml", "docker-compose.yaml", ".env.example", ".env.sample",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "Gemfile", "composer.json",
]

VALID_DB_TYPES = {"postgresql", "mysql", "mariadb", "mongodb", "redis"}


class DBAAdvisor:
    """Uses AI to determine database requirements when static scan is ambiguous."""

    def __init__(self):
        pass

    def advise(self, repo_path: str) -> list[DBRequirement]:
        """Ask the AI to analyze database requirements for the repository."""
        file_tree = self._get_file_tree(repo_path)
        key_files = self._read_key_files(repo_path)

        prompt = f"""You are a database architect. Analyze this repository and determine exactly what database(s) it needs to run.

File tree:
{file_tree}

Key files:
{key_files}

Rules:
1. If no external database is needed (static site, pure in-memory app), set needs_database to false.
2. If SQLite is used, set needs_database to false — no container is needed for SQLite.
3. Choose db_type from: "postgresql", "mysql", "mariadb", "mongodb", "redis" only.
4. A repo may need more than one database (e.g. PostgreSQL + Redis). List all of them.
5. Rate your confidence per database from 0.0 (guessing) to 1.0 (certain).

Return ONLY this JSON:
{{
  "needs_database": true,
  "databases": [
    {{
      "db_type": "postgresql",
      "confidence": 0.92,
      "reasoning": "psycopg2 in requirements.txt and DATABASE_URL=postgres in .env.example"
    }}
  ]
}}"""

        try:
            ai = AIClient()
            result = ai.generate_json(prompt, max_tokens=256)

            if not result.get("needs_database"):
                return []

            requirements: list[DBRequirement] = []
            for db in result.get("databases", []):
                db_type = db.get("db_type", "").lower()
                if db_type not in VALID_DB_TYPES:
                    continue

                confidence = float(db.get("confidence", 0.5))
                confidence = max(0.0, min(1.0, confidence))

                requirements.append(
                    DBRequirement(
                        needs_database=True,
                        db_type=db_type,
                        confidence=confidence,
                        detection_source="ai_advised",
                    )
                )

            return requirements

        except Exception:
            return []

    def _get_file_tree(self, repo_path: str, max_depth: int = 3) -> str:
        lines: list[str] = []
        self._walk(repo_path, lines, max_depth, 0)
        return "\n".join(lines[:300])

    def _walk(self, path: str, lines: list[str], max_depth: int, depth: int) -> None:
        if depth >= max_depth or len(lines) > 300:
            return
        try:
            entries = sorted(os.listdir(path))
        except OSError:
            return
        indent = "  " * depth
        for e in entries:
            full = os.path.join(path, e)
            if os.path.isdir(full):
                if e not in SKIP_DIRS and not e.startswith("."):
                    lines.append(f"{indent}{e}/")
                    self._walk(full, lines, max_depth, depth + 1)
            else:
                lines.append(f"{indent}{e}")

    def _read_key_files(self, repo_path: str) -> str:
        sections: list[str] = []
        for filename in KEY_FILES:
            filepath = os.path.join(repo_path, filename)
            if not os.path.isfile(filepath):
                continue
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(500)
                sections.append(f"=== {filename} ===\n{content}\n")
            except OSError:
                continue
        return "\n".join(sections) if sections else "(no key files found)"
