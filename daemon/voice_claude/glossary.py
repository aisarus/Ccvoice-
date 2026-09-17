"""Подсказка имён для голосового ввода.

Распознаватель не знает, что «аус ти эс» — это `auth.ts`, а «тейлскейл» —
Tailscale. Переписывать за него реплику нельзя (спека: испорченный телефон),
но можно приложить список имён, которые в этом проекте вообще встречаются,
и дать Claude исправить очевидное по контексту — как он и делает с опечатками.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "build", "dist",
             ".gradle", ".idea", "venv", "target"}
INTERESTING = {".py", ".ts", ".tsx", ".js", ".kt", ".java", ".go", ".rs", ".rb",
               ".sh", ".yml", ".yaml", ".json", ".toml", ".md", ".html", ".css"}
WORD_RE = re.compile(r"^[\w.-]{3,40}$")


def collect(workspace: str | Path, limit: int = 40, depth: int = 3) -> list[str]:
    """Имена файлов и каталогов проекта — то, что человек называет вслух."""
    root = Path(workspace).expanduser()
    if not root.is_dir():
        return []
    names: list[str] = []
    seen: set[str] = set()
    root_depth = len(root.parts)

    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        if len(Path(current).parts) - root_depth >= depth:
            dirs[:] = []
        for name in list(dirs) + files:
            if name in seen or not WORD_RE.match(name):
                continue
            if name in files and Path(name).suffix not in INTERESTING:
                continue
            seen.add(name)
            names.append(name)
            if len(names) >= limit * 3:
                break
        if len(names) >= limit * 3:
            break

    # Короткие и часто произносимые имена полезнее длинных путей.
    names.sort(key=lambda n: (len(n), n))
    return names[:limit]


def extra_terms() -> list[str]:
    """Свои слова: имена проектов, людей, сервисов — через VOICE_GLOSSARY."""
    raw = os.environ.get("VOICE_GLOSSARY", "")
    return [term.strip() for term in raw.split(",") if term.strip()]


def hint_line(terms: list[str]) -> str:
    if not terms:
        return ""
    return ("[глоссарий] в этом проекте встречаются: " + ", ".join(terms) +
            ". Если в реплике что-то звучит похоже на одно из этих имён, "
            "считай, что имелось в виду оно.")


def for_workspace(workspace: str | Path, limit: int = 40) -> str:
    terms = extra_terms() + [t for t in collect(workspace, limit) if t not in extra_terms()]
    return hint_line(terms[:limit])
