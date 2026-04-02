"""Language/framework auto-detection from repository file structure."""

import glob
import os
from typing import Optional


class StackDetector:
    """Detects the primary language/framework stack of a repository."""

    INDICATORS: dict[str, list[str]] = {
        "python-poetry": ["pyproject.toml", "poetry.lock"],
        "python-conda": ["environment.yml", "conda.yml"],
        "python-pip": ["requirements.txt"],
        "node": ["package.json"],
        "rust": ["Cargo.toml"],
        "go": ["go.mod"],
        "java-gradle": ["build.gradle", "build.gradle.kts"],
        "java-maven": ["pom.xml"],
        "ruby": ["Gemfile"],
        "php": ["composer.json"],
        "dotnet": ["*.csproj", "*.fsproj", "*.sln"],
        "elixir": ["mix.exs"],
        "c-cpp": ["CMakeLists.txt", "Makefile", "*.c", "*.cpp", "*.h"],
        "scala": ["build.sbt"],
        "kotlin": ["*.kt", "build.gradle.kts"],
        "deno": ["deno.json", "deno.jsonc", "deno.lock"],
        "bun": ["bun.lockb", "bunfig.toml"],
        "static-site": ["index.html"],
        "docker": ["Dockerfile"],
    }

    # Priority order: more specific stacks first
    PRIORITY = [
        "python-poetry",
        "python-conda",
        "python-pip",
        "deno",
        "bun",
        "node",
        "rust",
        "go",
        "scala",
        "kotlin",
        "java-gradle",
        "java-maven",
        "ruby",
        "php",
        "dotnet",
        "elixir",
        "c-cpp",
        "static-site",
        "docker",
    ]

    @classmethod
    def detect(cls, repo_path: str) -> str:
        """Detect the primary stack of a repository.

        Checks for indicator files in the repo root and one level deep.
        Returns the best match or 'generic'.
        """
        matches: dict[str, int] = {}

        for stack in cls.PRIORITY:
            indicators = cls.INDICATORS[stack]
            score = 0
            for indicator in indicators:
                # Check root
                if cls._file_exists(repo_path, indicator):
                    score += 2  # Root match is stronger

                # Check one level deep
                for entry in cls._list_subdirs(repo_path):
                    subdir = os.path.join(repo_path, entry)
                    if cls._file_exists(subdir, indicator):
                        score += 1

            if score > 0:
                matches[stack] = score

        if not matches:
            return "generic"

        # Return highest scoring stack, respecting priority order for ties
        max_score = max(matches.values())
        for stack in cls.PRIORITY:
            if matches.get(stack) == max_score:
                return stack

        return "generic"

    @staticmethod
    def _file_exists(directory: str, pattern: str) -> bool:
        """Check if a file matching the pattern exists in the directory."""
        if "*" in pattern:
            return len(glob.glob(os.path.join(directory, pattern))) > 0
        return os.path.isfile(os.path.join(directory, pattern))

    @staticmethod
    def _list_subdirs(repo_path: str) -> list[str]:
        """List immediate subdirectories, skipping hidden and common junk dirs."""
        skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "target", "dist", "build", ".tox"}
        try:
            return [
                d for d in os.listdir(repo_path)
                if os.path.isdir(os.path.join(repo_path, d)) and d not in skip
            ]
        except OSError:
            return []
