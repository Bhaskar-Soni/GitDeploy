"""Database requirement detection from repository files via static analysis."""

import os
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DBRequirement:
    needs_database: bool = False
    db_type: Optional[str] = None
    detection_source: str = "static_scan"
    confidence: float = 0.0


DB_SIGNALS: dict[str, dict] = {
    "postgresql": {
        "file_patterns": ["docker-compose.yml", "docker-compose.yaml", ".env.example", ".env.sample"],
        "content_patterns": [
            r"postgres(?:ql)?",
            r"DATABASE_URL\s*=\s*postgres",
            r"\bpg\b",
            r"psycopg",
            r"asyncpg",
            r"sequelize.*dialect.*postgres",
            r"knex.*client.*pg",
            r"typeorm.*postgres",
            r"django.*postgresql",
            r"spring\.datasource\.url.*postgresql",
        ],
        "dependency_keys": {
            "package.json": ["pg", "pg-promise", "sequelize", "typeorm", "knex", "prisma"],
            "requirements.txt": ["psycopg2", "psycopg", "asyncpg", "databases[postgresql]"],
            "pyproject.toml": ["psycopg2", "asyncpg", "psycopg"],
            "Gemfile": ["pg", "activerecord"],
            "pom.xml": ["postgresql"],
            "go.mod": ["lib/pq", "pgx"],
        },
    },
    "mysql": {
        "file_patterns": ["docker-compose.yml", "docker-compose.yaml", ".env.example"],
        "content_patterns": [
            r"mysql(?!ite)",
            r"DATABASE_URL\s*=\s*mysql",
            r"pymysql",
            r"mysql2",
            r"mysql-connector",
            r"sequelize.*dialect.*mysql",
            r"django.*mysql",
        ],
        "dependency_keys": {
            "package.json": ["mysql", "mysql2", "sequelize"],
            "requirements.txt": ["pymysql", "mysql-connector-python", "mysqlclient"],
            "Gemfile": ["mysql2"],
            "pom.xml": ["mysql-connector-java"],
            "go.mod": ["go-sql-driver/mysql"],
        },
    },
    "mariadb": {
        "file_patterns": ["docker-compose.yml", "docker-compose.yaml"],
        "content_patterns": [r"mariadb", r"DATABASE_URL\s*=\s*mariadb"],
        "dependency_keys": {
            "package.json": ["mariadb"],
            "requirements.txt": ["mariadb"],
        },
    },
    "mongodb": {
        "file_patterns": ["docker-compose.yml", "docker-compose.yaml", ".env.example"],
        "content_patterns": [
            r"mongodb",
            r"mongoose",
            r"MONGO_URI",
            r"MONGODB_URI",
            r"MONGO_URL",
            r"pymongo",
            r"motor",
            r"spring\.data\.mongodb",
        ],
        "dependency_keys": {
            "package.json": ["mongoose", "mongodb"],
            "requirements.txt": ["pymongo", "motor", "beanie"],
            "pyproject.toml": ["pymongo", "motor"],
            "Gemfile": ["mongoid", "mongo"],
            "pom.xml": ["spring-boot-starter-data-mongodb"],
            "go.mod": ["mongo-driver"],
        },
    },
    "redis": {
        "file_patterns": ["docker-compose.yml", "docker-compose.yaml", ".env.example"],
        "content_patterns": [
            r"redis",
            r"REDIS_URL",
            r"REDIS_HOST",
            r"celery.*broker.*redis",
            r"bull",
            r"ioredis",
        ],
        "dependency_keys": {
            "package.json": ["redis", "ioredis", "bull", "bullmq"],
            "requirements.txt": ["redis", "aioredis", "celery[redis]"],
            "Gemfile": ["redis"],
            "go.mod": ["go-redis"],
        },
    },
    "sqlite": {
        "file_patterns": [],
        "content_patterns": [
            r"sqlite",
            r"DATABASE_URL\s*=\s*sqlite",
            r"django.*sqlite3",
        ],
        "dependency_keys": {
            "package.json": ["better-sqlite3", "sqlite3"],
            "Gemfile": ["sqlite3"],
        },
    },
}

# Source file extensions to scan for content patterns
SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rb", ".java", ".rs", ".php"}

# Directories to skip during scanning
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "target", "dist", "build", ".tox", ".mypy_cache"}


class DBDetector:
    """Detects database requirements from repository files using static analysis."""

    def detect(self, repo_path: str) -> list[DBRequirement]:
        """Scan the repository and return detected database requirements.

        Returns a list of DBRequirement sorted by confidence (highest first).
        """
        results: dict[str, float] = {}

        for db_type, signals in DB_SIGNALS.items():
            confidence = 0.0

            # Check dependency files for known packages (high confidence)
            dep_conf = self._check_dependencies(repo_path, signals.get("dependency_keys", {}))
            if dep_conf > 0:
                confidence = max(confidence, 0.95)

            # Check content patterns in relevant files
            content_conf = self._check_content_patterns(
                repo_path, signals.get("content_patterns", [])
            )
            confidence = max(confidence, min(content_conf, 0.9))

            if confidence > 0:
                results[db_type] = confidence

        # SQLite doesn't need a container
        if "sqlite" in results:
            del results["sqlite"]

        # Filter low confidence
        results = {k: v for k, v in results.items() if v >= 0.3}

        if not results:
            return [DBRequirement(needs_database=False)]

        # De-duplicate: if both mysql and postgresql detected (e.g. via shared ORM
        # like sequelize/typeorm), pick the one with the strongest signal.
        # Only keep both if the project explicitly uses BOTH (e.g. separate drivers).
        _sql_dbs = {"postgresql", "mysql", "mariadb"}
        _detected_sql = {k: v for k, v in results.items() if k in _sql_dbs}
        if len(_detected_sql) > 1:
            # Check if the project has a DB-specific driver (not just an ORM)
            _has_specific_driver = {}
            _specific_drivers = {
                "postgresql": {"pg", "pg-promise", "psycopg2", "psycopg", "asyncpg", "pgx", "lib/pq"},
                "mysql": {"mysql", "mysql2", "pymysql", "mysql-connector-python", "mysqlclient", "go-sql-driver/mysql"},
                "mariadb": {"mariadb"},
            }
            for db_type in _detected_sql:
                drivers = _specific_drivers.get(db_type, set())
                if self._has_specific_packages(repo_path, drivers):
                    _has_specific_driver[db_type] = True

            if len(_has_specific_driver) == 1:
                # Only one has a specific driver — keep only that one
                keep = list(_has_specific_driver.keys())[0]
                for db_type in list(_detected_sql.keys()):
                    if db_type != keep:
                        del results[db_type]
            elif len(_has_specific_driver) == 0:
                # No specific drivers — keep only the highest confidence SQL DB
                best = max(_detected_sql, key=_detected_sql.get)
                for db_type in list(_detected_sql.keys()):
                    if db_type != best:
                        del results[db_type]

        # Build requirements list
        requirements: list[DBRequirement] = []
        for db_type, confidence in sorted(results.items(), key=lambda x: x[1], reverse=True):
            if confidence >= 0.7:
                source = "static_scan"
            elif confidence >= 0.5:
                source = "ai_advised"  # Caller should use AI advisor
            else:
                continue

            requirements.append(
                DBRequirement(
                    needs_database=True,
                    db_type=db_type,
                    detection_source=source,
                    confidence=confidence,
                )
            )

        if not requirements:
            return [DBRequirement(needs_database=False)]

        return requirements

    def _check_dependencies(self, repo_path: str, dependency_keys: dict[str, list[str]]) -> float:
        """Check known dependency/config files for database package references."""
        for config_file, packages in dependency_keys.items():
            file_path = os.path.join(repo_path, config_file)
            if not os.path.isfile(file_path):
                continue

            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read().lower()
            except OSError:
                continue

            for pkg in packages:
                if pkg.lower() in content:
                    return 0.95

        return 0.0

    def _check_content_patterns(self, repo_path: str, patterns: list[str]) -> float:
        """Scan source and config files for regex patterns. Each match adds 0.2, capped at 0.9."""
        if not patterns:
            return 0.0

        compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
        score = 0.0
        files_to_scan = self._collect_scannable_files(repo_path)

        for file_path in files_to_scan:
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(50_000)  # Read up to 50KB per file
            except OSError:
                continue

            for pattern in compiled:
                if pattern.search(content):
                    score += 0.2
                    if score >= 0.9:
                        return 0.9

        return score

    def _collect_scannable_files(self, repo_path: str, max_depth: int = 3) -> list[str]:
        """Collect files to scan: config files + source files up to max_depth."""
        files: list[str] = []

        # Always scan these config files at root
        config_files = [
            ".env.example", ".env.sample", ".env",
            "docker-compose.yml", "docker-compose.yaml",
            "README.md", "readme.md",
        ]
        for cf in config_files:
            path = os.path.join(repo_path, cf)
            if os.path.isfile(path):
                files.append(path)

        # Walk directory up to max_depth for source files
        for root, dirs, filenames in os.walk(repo_path):
            # Calculate depth
            rel = os.path.relpath(root, repo_path)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth >= max_depth:
                dirs.clear()
                continue

            # Skip unwanted directories
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

            for fn in filenames:
                ext = os.path.splitext(fn)[1]
                if ext in SOURCE_EXTENSIONS:
                    files.append(os.path.join(root, fn))

        return files[:200]  # Cap at 200 files to avoid slow scans

    def _has_specific_packages(self, repo_path: str, driver_names: set[str]) -> bool:
        """Check if the repo has any of the given DB-specific driver packages installed."""
        dep_files = ["package.json", "requirements.txt", "pyproject.toml", "Gemfile",
                      "pom.xml", "go.mod", "composer.json"]
        for df in dep_files:
            path = os.path.join(repo_path, df)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read().lower()
            except OSError:
                continue
            for driver in driver_names:
                if driver.lower() in content:
                    return True
        return False
