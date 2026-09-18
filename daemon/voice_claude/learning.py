"""Роутер, который учится на поправках.

Классификатор по словам ошибается на живой речи, а человек поправляет одинаково:
«не туда». Эту поправку надо не терять, а превращать в пример — тогда та же
фраза во второй раз уедет правильно.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import lexicon

STOP_WORDS = set(lexicon.every("stopwords"))


@dataclass
class Example:
    text: str
    target: str
    ts: float


def tokens(text: str) -> set[str]:
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    return {w for w in words if len(w) > 2 and w not in STOP_WORDS}


def similarity(left: str, right: str) -> float:
    """Доля общих слов — простая мера, но достаточная для коротких реплик."""
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class Examples:
    """Поправки человека, переживающие перезапуск."""

    def __init__(self, workspace: str | Path, path: str | Path | None = None,
                 threshold: float = 0.6, limit: int = 200) -> None:
        self.path = Path(path) if path else Path(workspace) / ".voice-shell" / "routes.json"
        self.threshold = threshold
        self.limit = limit
        self._items: list[Example] = []
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._items = [Example(**item) for item in raw][-self.limit:]
        except (OSError, ValueError, TypeError):
            self._items = []

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps([asdict(i) for i in self._items[-self.limit:]], ensure_ascii=False, indent=1),
                encoding="utf-8")
        except OSError:
            pass

    def remember(self, text: str, target: str) -> None:
        if not tokens(text):
            return
        # Та же фраза с другим ответом заменяет прошлый пример, а не спорит с ним.
        self._items = [i for i in self._items if similarity(i.text, text) < 0.9]
        self._items.append(Example(text=text.strip(), target=target, ts=time.time()))
        self._save()

    def suggest(self, text: str) -> tuple[str, float] | None:
        """Цель из прошлой поправки, если реплика достаточно похожа."""
        best: tuple[str, float] | None = None
        for item in self._items:
            score = similarity(item.text, text)
            if score >= self.threshold and (best is None or score > best[1]):
                best = (item.target, score)
        return best

    def __len__(self) -> int:
        return len(self._items)
