#!/usr/bin/env python3
"""Block Codex edits to secrets and credential material."""

from __future__ import annotations

import json
import re
import sys
from pathlib import PurePath
from typing import Any

PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
MOVE_PATH = re.compile(r"^\*\*\* Move to: (.+)$", re.MULTILINE)
ALLOWLIST = (".env.example", ".env.template")


def affected_paths(data: dict[str, Any]) -> set[str]:
    tool_input = data.get("tool_input") or {}
    paths: set[str] = set()
    if isinstance(tool_input, dict):
        for key in ("file_path", "path"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                paths.add(value.strip())
        patch = tool_input.get("patch")
        if isinstance(patch, str):
            paths.update(PATCH_PATH.findall(patch))
            paths.update(MOVE_PATH.findall(patch))
    return {path.strip().strip('"\'') for path in paths}


def protected(path: str) -> bool:
    norm = path.replace("\\", "/").lower()
    name = PurePath(norm).name
    if norm.endswith(ALLOWLIST):
        return False
    if name == ".env" or name.startswith(".env.") or name.endswith(".env"):
        return True
    if name.endswith((".pem", ".key")):
        return True
    return bool(re.search(r"credentials?|secrets?", norm))


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError):
        return 0
    blocked = sorted(path for path in affected_paths(data) if protected(path))
    if not blocked:
        return 0
    print("BLOCKED: protected path(s): " + ", ".join(blocked), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

