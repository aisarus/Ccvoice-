"""Точки отката.

Голосом легко сказать лишнее, а в авто-режиме никто не переспросит. Поэтому
каждая реплика, после которой в проекте что-то изменилось, закрывается
коммитом, а «Клод, откати последнее» возвращает состояние до неё.

Ничего не теряется безвозвратно: отменённый коммит остаётся в истории git и в
журнале точек, так что «верни обратно» тоже возможно.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

JOURNAL = "checkpoints.json"
UNDO_PHRASES = (
    "откати последнее", "откати", "отмени последнее", "отмени изменения",
    "верни как было", "верни обратно", "отмена последнего",
)
HISTORY_PHRASES = (
    "что ты сделал", "что ты наделал", "покажи последние изменения",
    "какие были изменения", "что изменилось",
)


@dataclass
class Checkpoint:
    before: str
    after: str
    utterance: str
    ts: float

    @property
    def title(self) -> str:
        text = self.utterance.strip()
        return text if len(text) <= 60 else text[:57] + "…"


def _git(workspace: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True, text=True, timeout=30,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)}")
    return result.stdout.strip()


def is_repo(workspace: str | Path) -> bool:
    path = Path(workspace)
    if not path.is_dir():
        return False
    try:
        return _git(path, "rev-parse", "--is-inside-work-tree") == "true"
    except (RuntimeError, OSError):
        return False


def head(workspace: str | Path) -> str:
    return _git(Path(workspace), "rev-parse", "HEAD")


def has_changes(workspace: str | Path) -> bool:
    return bool(_git(Path(workspace), "status", "--porcelain"))


def commit_all(workspace: str | Path, message: str) -> str | None:
    """Складывает всё изменённое в один коммит. None — менять было нечего."""
    path = Path(workspace)
    if not has_changes(path):
        return None
    _git(path, "add", "-A")
    _git(path, "-c", "user.name=Voice Shell", "-c", "user.email=voice@shell.local",
         "commit", "-m", message, "--no-verify")
    return head(path)


def reset_to(workspace: str | Path, commit: str) -> None:
    _git(Path(workspace), "reset", "--hard", commit)


def summary(workspace: str | Path, before: str, after: str) -> str:
    """Человеческое описание изменения: «auth.ts и ещё два файла»."""
    files = [line for line in
             _git(Path(workspace), "diff", "--name-only", f"{before}..{after}").splitlines() if line]
    if not files:
        return "без изменений в файлах"
    names = [Path(f).name for f in files]
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} и {names[1]}"
    return f"{names[0]}, {names[1]} и ещё {len(names) - 2}"


class Journal:
    """Стопка точек отката, переживающая перезапуск демона."""

    def __init__(self, workspace: str | Path, path: str | Path | None = None) -> None:
        self.workspace = Path(workspace)
        self.path = Path(path) if path else self.workspace / ".voice-shell" / JOURNAL
        self._items: list[Checkpoint] = []
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._items = [Checkpoint(**item) for item in raw][-50:]
        except (OSError, ValueError, TypeError):
            self._items = []

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([asdict(i) for i in self._items[-50:]],
                                            ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def add(self, before: str, after: str, utterance: str) -> Checkpoint:
        point = Checkpoint(before=before, after=after, utterance=utterance, ts=time.time())
        self._items.append(point)
        self._save()
        return point

    def last(self) -> Checkpoint | None:
        return self._items[-1] if self._items else None

    def pop(self) -> Checkpoint | None:
        if not self._items:
            return None
        point = self._items.pop()
        self._save()
        return point

    def recent(self, limit: int = 5) -> list[Checkpoint]:
        return list(reversed(self._items[-limit:]))


def matches(text: str, phrases: tuple[str, ...]) -> bool:
    lowered = " ".join(text.lower().replace(",", " ").split())
    return any(lowered == phrase or lowered.startswith(phrase + " ") or phrase in lowered
               for phrase in phrases)
