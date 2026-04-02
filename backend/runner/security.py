"""Command sanitization and blocklist for sandbox security."""

import re
from typing import ClassVar


class SecurityChecker:
    """Validates and filters shell commands to prevent dangerous operations."""

    BLOCKED_PATTERNS: ClassVar[list[str]] = [
        r"rm\s+-rf\s+/",
        r"rm\s+-fr\s+/",
        r"curl\s+.*\|\s*(bash|sh|zsh)",
        r"wget\s+.*\|\s*(bash|sh|zsh)",
        r"sudo\s+",
        r"chmod\s+777",
        r">\s*/etc/",
        r">\s*/usr/",
        r">\s*/bin/",
        r">\s*/sbin/",
        r">\s*/var/",
        r"mkfs\.",
        r"dd\s+if=",
        r":\(\)\{.*\}",          # Fork bomb
        r"base64\s+-d.*\|",
        r"\beval\b.*\$\(",       # eval with command substitution
        r"\/dev\/sd[a-z]",       # Raw device access
        r"iptables",
        r"shutdown",
        r"reboot",
        r"init\s+[0-6]",
        r"kill\s+-9\s+-1",       # Kill all processes
        r">\s*/dev/",
        r"nc\s+-[elp]",         # Netcat listeners
    ]

    _compiled: ClassVar[list[re.Pattern]] = [
        re.compile(p, re.IGNORECASE) for p in BLOCKED_PATTERNS
    ]

    @classmethod
    def filter(cls, commands: list[str]) -> list[str]:
        """Return only safe commands from the input list."""
        safe: list[str] = []
        for cmd in commands:
            if not isinstance(cmd, str):
                continue
            cmd = cmd.strip()
            if not cmd:
                continue
            if cls.is_safe(cmd):
                safe.append(cmd)
        return safe

    @classmethod
    def is_safe(cls, command: str) -> bool:
        """Check if a single command is safe to execute."""
        for pattern in cls._compiled:
            if pattern.search(command):
                return False
        return True
