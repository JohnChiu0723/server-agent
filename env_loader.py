"""Minimal .env loader (no third-party dependency).

Loads ``<project root>/.env`` into ``os.environ`` without overriding variables
that are already set by the real environment.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def load_env(path: Path | None = None) -> None:
    env_file = path or (PROJECT_ROOT / ".env")
    if not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def require_env(name: str) -> str:
    """Return a mandatory environment variable or raise a clear error."""
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name} (see .env.example)")
    return value
