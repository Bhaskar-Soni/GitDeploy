"""Self-learning error knowledge base.

When the AI fixes a build/runtime error and the rebuild succeeds, the pattern is
stored permanently in the database. Future deployments check the KB first — no AI
call needed for errors we've seen before.

Storage: AppSetting table, key='error_kb', value=JSON list of fix entries.
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Seed data — grows automatically as AI learns new fixes ───────────────────
_SEED_FIXES = [
    {
        "pattern": r"urllib3\.packages\.six|No module named 'urllib3\.packages'",
        "pip": ["__UPGRADE__ requests urllib3>=1.26.0,<3"],
        "apt": [],
        "explanation": "urllib3 1.25.x is incompatible with Python 3.12 — upgrade requests + urllib3>=1.26",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'six'",
        "pip": ["six"],
        "apt": [],
        "explanation": "Missing 'six' compatibility package",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'cv2'",
        "pip": ["opencv-python-headless"],
        "apt": ["libgl1", "libglib2.0-0"],
        "explanation": "OpenCV requires opencv-python-headless + libgl1",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'PIL'",
        "pip": ["Pillow"],
        "apt": [],
        "explanation": "PIL is the old name — install Pillow",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'distutils'",
        "pip": ["setuptools"],
        "apt": [],
        "explanation": "distutils removed in Python 3.12 — setuptools provides it",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'yaml'|No module named 'ruamel'",
        "pip": ["pyyaml"],
        "apt": [],
        "explanation": "yaml module comes from PyYAML",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'dotenv'|No module named 'python_dotenv'",
        "pip": ["python-dotenv"],
        "apt": [],
        "explanation": "dotenv module comes from python-dotenv",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'bs4'",
        "pip": ["beautifulsoup4"],
        "apt": [],
        "explanation": "bs4 module comes from beautifulsoup4",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'tqdm'",
        "pip": ["tqdm"],
        "apt": [],
        "explanation": "Missing tqdm progress bar library",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'rich'",
        "pip": ["rich"],
        "apt": [],
        "explanation": "Missing rich terminal formatting library",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'colorama'",
        "pip": ["colorama"],
        "apt": [],
        "explanation": "Missing colorama terminal colors library",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'aiohttp'",
        "pip": ["aiohttp"],
        "apt": [],
        "explanation": "Missing aiohttp async HTTP library",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'httpx'",
        "pip": ["httpx"],
        "apt": [],
        "explanation": "Missing httpx HTTP library",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'tabulate'",
        "pip": ["tabulate"],
        "apt": [],
        "explanation": "Missing tabulate table formatting library",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"libGL\.so|libGL error|cannot open shared object file.*libGL",
        "pip": [],
        "apt": ["libgl1", "libglib2.0-0"],
        "explanation": "libGL system library required for graphics/OpenCV",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"cannot import name 'validator' from 'pydantic'|pydantic.*BaseSettings.*cannot import",
        "pip": ["pydantic==1.10.21", "pydantic-settings"],
        "apt": [],
        "explanation": "Pydantic v2 breaking change — pinned to v1 + pydantic-settings",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'cryptography'",
        "pip": ["cryptography"],
        "apt": [],
        "explanation": "Missing cryptography package",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"No module named 'lxml'",
        "pip": ["lxml"],
        "apt": ["libxml2-dev", "libxslt1-dev"],
        "explanation": "lxml requires system XML libraries",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"E: Unable to locate package libgl1-mesa-glx",
        "pip": [],
        "apt": ["libgl1"],
        "explanation": "libgl1-mesa-glx was renamed to libgl1 in Debian bookworm",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"E: Unable to locate package libegl1-mesa",
        "pip": [],
        "apt": ["libegl1"],
        "explanation": "libegl1-mesa was renamed to libegl1 in Debian bookworm",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"E: Unable to locate package libext6",
        "pip": [],
        "apt": ["libxext6"],
        "explanation": "Wrong package name — libext6 should be libxext6",
        "times_applied": 0,
        "times_succeeded": 0,
    },
    {
        "pattern": r"ModuleNotFoundError: No module named '(\w+)'",
        "pip": [],  # dynamic — extracted at match time
        "apt": [],
        "explanation": "Auto-learned: missing Python module",
        "times_applied": 0,
        "times_succeeded": 0,
        "_dynamic": True,  # special flag: extract module name from match group
    },
]


class ErrorKnowledgeBase:
    """Persistent, self-learning fix database. Singleton loaded at import time."""

    _instance: Optional["ErrorKnowledgeBase"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._fixes = list(_SEED_FIXES)
            cls._instance._loaded = False
        return cls._instance

    def _load_from_db(self):
        """Load learned fixes from AppSetting DB (sync via psycopg2)."""
        if self._loaded:
            return
        self._loaded = True
        try:
            import os
            import psycopg2
            db_url = os.environ.get("SYNC_DATABASE_URL", "postgresql://gitdeploy:gitdeploy@postgres:5432/gitdeploy")
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            cur.execute("SELECT value FROM app_settings WHERE key = 'error_kb'")
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                learned = json.loads(row[0])
                # Merge: add any entries not already in seed (match by pattern)
                existing_patterns = {f["pattern"] for f in self._fixes}
                for entry in learned:
                    if entry.get("pattern") not in existing_patterns:
                        self._fixes.append(entry)
                    else:
                        # Update counters for seed entries
                        for f in self._fixes:
                            if f["pattern"] == entry["pattern"]:
                                f["times_applied"] = entry.get("times_applied", 0)
                                f["times_succeeded"] = entry.get("times_succeeded", 0)
                                break
        except Exception as e:
            logger.debug(f"ErrorKB: could not load from DB: {e}")

    def _save_to_db(self):
        """Persist learned fixes back to AppSetting."""
        try:
            import os
            import psycopg2
            db_url = os.environ.get("SYNC_DATABASE_URL", "postgresql://gitdeploy:gitdeploy@postgres:5432/gitdeploy")
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            # Filter out dynamic seed entries with no real pattern for storage
            storable = [f for f in self._fixes if not f.get("_dynamic") or f.get("times_succeeded", 0) > 0]
            cur.execute(
                "INSERT INTO app_settings (key, value) VALUES ('error_kb', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (json.dumps(storable),),
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.debug(f"ErrorKB: could not save to DB: {e}")

    def lookup(self, error_text: str) -> Optional[tuple[list[str], list[str], str]]:
        """Check if any known pattern matches. Returns (pip_pkgs, apt_pkgs, explanation) or None."""
        self._load_from_db()
        for fix in self._fixes:
            m = re.search(fix["pattern"], error_text, re.IGNORECASE)
            if m:
                pip_pkgs = list(fix.get("pip", []))
                apt_pkgs = list(fix.get("apt", []))
                # Dynamic: extract the module name from match group
                if fix.get("_dynamic") and m.lastindex and m.lastindex >= 1:
                    module_name = m.group(1)
                    if pip_pkgs == [] and apt_pkgs == []:
                        pip_pkgs = [module_name]
                if pip_pkgs or apt_pkgs:
                    fix["times_applied"] = fix.get("times_applied", 0) + 1
                    return pip_pkgs, apt_pkgs, fix["explanation"]
        return None

    def learn(self, error_text: str, original_dockerfile: str, fixed_dockerfile: str, explanation: str):
        """Extract and store a new fix pattern learned from a successful AI fix."""
        self._load_from_db()
        try:
            pip_added, apt_added = _diff_dockerfile_packages(original_dockerfile, fixed_dockerfile)
            if not pip_added and not apt_added:
                return  # Nothing useful to learn

            # Extract error signature — most specific first
            pattern = _extract_error_pattern(error_text)
            if not pattern:
                return

            # Check if we already know this pattern
            for fix in self._fixes:
                if fix["pattern"] == pattern:
                    fix["times_succeeded"] = fix.get("times_succeeded", 0) + 1
                    self._save_to_db()
                    return

            # New pattern — add it
            new_entry = {
                "pattern": pattern,
                "pip": pip_added,
                "apt": apt_added,
                "explanation": explanation or f"AI learned: {pattern}",
                "times_applied": 1,
                "times_succeeded": 1,
            }
            self._fixes.append(new_entry)
            logger.info(f"ErrorKB: learned new fix — pattern='{pattern}' pip={pip_added} apt={apt_added}")
            self._save_to_db()
        except Exception as e:
            logger.debug(f"ErrorKB: learn failed: {e}")

    def mark_succeeded(self, error_text: str):
        """Mark a KB hit as successfully resolved."""
        for fix in self._fixes:
            if re.search(fix["pattern"], error_text, re.IGNORECASE):
                fix["times_succeeded"] = fix.get("times_succeeded", 0) + 1
                self._save_to_db()
                return

    def stats(self) -> dict:
        return {
            "total_patterns": len(self._fixes),
            "top_fixes": sorted(
                [{"pattern": f["pattern"][:60], "applied": f.get("times_applied", 0), "succeeded": f.get("times_succeeded", 0)}
                 for f in self._fixes if f.get("times_applied", 0) > 0],
                key=lambda x: x["applied"],
                reverse=True,
            )[:10],
        }


def _diff_dockerfile_packages(original: str, fixed: str) -> tuple[list[str], list[str]]:
    """Extract pip/apt packages added in fixed vs original Dockerfile."""
    orig_lines = set(original.splitlines())
    new_lines = [l for l in fixed.splitlines() if l not in orig_lines]

    pip_added = []
    apt_added = []
    for line in new_lines:
        line = line.strip()
        pip_m = re.search(r"pip install[^&\n]+([\w<>=!.,\[\]-]+)", line)
        if pip_m:
            # Extract individual package tokens
            pkgs = re.findall(r"[\w][\w.<>=!,\[\]-]*", pip_m.group(0).replace("pip install", "").replace("--no-cache-dir", ""))
            pip_added.extend([p for p in pkgs if p and not p.startswith("-")])
        apt_m = re.search(r"apt-get install[^&\n]+", line)
        if apt_m:
            pkgs = re.findall(r"[\w][\w.-]*", apt_m.group(0).replace("apt-get install", "").replace("-y", ""))
            apt_added.extend([p for p in pkgs if p and not p.startswith("-")])

    return list(set(pip_added)), list(set(apt_added))


def _extract_error_pattern(error_text: str) -> Optional[str]:
    """Extract a reusable regex pattern from an error message."""
    # Most specific patterns first
    patterns_to_try = [
        # Python module errors
        (r"ModuleNotFoundError: No module named '([\w.]+)'", r"No module named '\1'"),
        (r"ImportError: cannot import name '(\w+)' from '([\w.]+)'", r"cannot import name '\1' from '\2'"),
        (r"ImportError: ([\w./ ]+ not found)", r"\1"),
        # Apt package not found
        (r"E: Unable to locate package (\S+)", r"E: Unable to locate package \1"),
        # Shared library errors
        (r"error while loading shared libraries: ([\w.]+)", r"error while loading shared libraries: \1"),
        # Generic import error
        (r"ImportError: (.+?)(?:\n|$)", r"\1"),
    ]
    for src_pattern, capture_template in patterns_to_try:
        m = re.search(src_pattern, error_text)
        if m:
            try:
                # Build the pattern from the captured groups
                result = capture_template
                for i, g in enumerate(m.groups(), 1):
                    result = result.replace(f"\\{i}", re.escape(g))
                return result
            except Exception:
                continue
    return None


# Module-level singleton
kb = ErrorKnowledgeBase()
