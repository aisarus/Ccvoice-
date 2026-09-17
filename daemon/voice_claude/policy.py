"""Заранее разрешённые действия.

Спека (`permissions.preapproved_policy`): спрашивать голосом про каждый `ls` —
издевательство, но список безопасного должен быть узким и явным, а опасное не
попадает в него никогда, даже если человек сказал «больше не спрашивай».
"""
from __future__ import annotations

import re
from typing import Any

# Инструменты, которые только читают.
SAFE_TOOLS = {"Read", "Grep", "Glob", "NotebookRead", "WebSearch", "WebFetch", "TodoWrite"}

# Команды оболочки, которые ничего не меняют и не выходят наружу.
SAFE_BASH = (
    r"^git\s+(status|diff|log|show|branch|remote\s+-v|stash\s+list)\b",
    r"^(ls|pwd|cat|head|tail|wc|find|grep|rg|tree|file|stat|du|df)\b",
    r"^(pytest|python\s+-m\s+pytest|npm\s+test|yarn\s+test|cargo\s+test|go\s+test)\b",
    r"^(ruff|flake8|eslint|mypy|black\s+--check|prettier\s+--check)\b",
    r"^(node|python3?)\s+--version\b",
)

# То, что не разрешается заранее никогда — ни политикой, ни «запомни это».
NEVER_PREAPPROVE = (
    r"\brm\s+-[rf]", r"\bgit\s+push\b.*--force", r"\bgit\s+reset\s+--hard",
    r"\bchmod\s+777\b", r"\bcurl\b[^|]*\|\s*(sh|bash)", r"\bdd\b",
    r"\bmkfs\b", r"\bshutdown\b", r"\breboot\b", r"\bkill\s+-9\b",
    r"\bsecret\b", r"\btoken\b", r"\bpassword\b", r"\.env\b",
    r"\bnpm\s+publish\b", r"\bpip\s+install\b", r"\bnpm\s+install\b",
)


def _command(tool: str, payload: dict[str, Any]) -> str:
    if tool == "Bash":
        return str(payload.get("command", "")).strip()
    return ""


def is_dangerous(tool: str, payload: dict[str, Any] | None = None) -> bool:
    """Опасное не уходит в автоматические разрешения ни при каких условиях."""
    haystack = f"{tool} {_command(tool, payload or {})}"
    return any(re.search(pattern, haystack, re.I) for pattern in NEVER_PREAPPROVE)


def decide(tool: str, payload: dict[str, Any] | None = None) -> str:
    """"allow" — делать молча, "ask" — спросить голосом."""
    payload = payload or {}
    if is_dangerous(tool, payload):
        return "ask"
    if tool in SAFE_TOOLS:
        return "allow"
    command = _command(tool, payload)
    if command and any(re.match(pattern, command, re.I) for pattern in SAFE_BASH):
        # Составные команды не разбираем: там легко спрятать что угодно.
        if not re.search(r"[;&|><`$]", command):
            return "allow"
    return "ask"


def may_remember(tool: str, payload: dict[str, Any] | None = None) -> bool:
    """Можно ли запомнить «больше не спрашивай» для этого действия."""
    return not is_dangerous(tool, payload)


MODES = ("ask", "auto", "bypass")


def mode() -> str:
    """Насколько молча работать: PERMISSION_MODE=ask|auto|bypass."""
    import os

    value = os.environ.get("PERMISSION_MODE", "auto").strip().lower()
    return value if value in MODES else "auto"


def decide_in_mode(tool: str, payload: dict[str, Any] | None = None) -> str:
    """Решение с учётом режима.

    ask    — как в спеке: безопасное молча, остальное голосом.
    auto   — молча всё, кроме разрушительного; оно спрашивается всегда.
    bypass — молча всё, без исключений.
    """
    current = mode()
    if current == "bypass":
        return "allow"
    if current == "auto":
        return "ask" if is_dangerous(tool, payload) else "allow"
    return decide(tool, payload)
