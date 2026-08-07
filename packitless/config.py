"""Environment loading for scripts and the CLI.

Deliberately dependency-free and deliberately *not* imported by the library.
`compress()` never reads a .env file — an application's secret handling is its
own business, and a compression library that quietly reads dotfiles is one
nobody should adopt. Entry points call `load_env()`; the library does not.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path | None = None, override: bool = False) -> list[str]:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    Args:
        path: File to read. Defaults to `.env` beside pyproject.toml.
        override: Whether to replace variables already set in the environment.
            Off by default so a real shell export always wins.

    Returns:
        Names of the variables that were set. Never the values.
    """
    path = path or DEFAULT_ENV_PATH
    if not path.exists():
        return []

    loaded: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and (override or key not in os.environ):
            os.environ[key] = value
            loaded.append(key)
    return loaded
