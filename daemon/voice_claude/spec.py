"""Access to spec/voice-shell.json — the single source of truth.

Thresholds, weights, routing rules and formatter limits are read from the spec
rather than duplicated in code, so `scripts/validate_spec.py` keeps guarding the
values the daemon actually runs on.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SPEC_PATH = Path(__file__).resolve().parents[2] / "spec" / "voice-shell.json"


@lru_cache(maxsize=4)
def load_spec(path: str | Path = SPEC_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def section(name: str, path: str | Path = SPEC_PATH) -> dict[str, Any]:
    return load_spec(path)[name]


def defaults(name: str, path: str | Path = SPEC_PATH) -> dict[str, Any]:
    return load_spec(path)["config_defaults"][name]
