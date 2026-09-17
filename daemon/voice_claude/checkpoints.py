"""Точки отката.

Голосом легко сказать лишнее, а в авто-режиме никто не переспросит. Поэтому
каждая реплика, после которой в проекте что-то изменилось, закрывается
коммитом, а «Клод, откати последнее» возвращает состояние до неё.

Ничего не теряется безвозвратно: отменённый коммит остаётся в истории git и в
журнале точек, так что «верни обратно» тоже возможно.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

JOURNAL = "checkpoints.json"
STATE_DIR = ".voice-shell"
# Мусор сборки: он никогда не коммитится нарочно, а голосом — тем более.
# Прячем локально, в .git/info/exclude: это личный список копии, он не
# уезжает к другим людям и не трогает .gitignore проекта. На уже
# отслеживаемые файлы он не влияет вовсе.
JUNK = ("__pycache__/", "*.pyc", ".pytest_cache/", "node_modules/",
        ".venv/", ".gradle/", ".DS_Store")
# Свой журнал в чужой коммит попадать не должен: он бы приезжал в каждый
# коммит проекта и в каждый пул-реквест.
OURS = (f":(exclude){STATE_DIR}", f":(exclude){STATE_DIR}/**")
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
    # `core.quotePath=false` — иначе git отдаёт нелатинские имена файлов
    # восьмеричными escape-последовательностями, и «Откатил файл.txt»
    # превращалось в «Откатил слэш триста двадцать...» прямо в ухо.
    result = subprocess.run(
        ["git", "-C", str(workspace), "-c", "core.quotePath=false", *args],
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
        inside = _git(path, "rev-parse", "--is-inside-work-tree") == "true"
    except (RuntimeError, OSError):
        return False
    if inside:
        ensure_ignored(path)
    return inside


def ensure_ignored(workspace: str | Path) -> None:
    """Спрятать служебный каталог локально, не трогая .gitignore проекта.

    `.git/info/exclude` — личный список хозяина копии: он не коммитится и не
    попадает к другим людям, а каталог перестаёт маячить в `git status`.
    """
    try:
        git_dir = Path(_git(Path(workspace), "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = Path(workspace) / git_dir
        exclude = git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        нужно = [line for line in (f"{STATE_DIR}/", *JUNK) if line not in current]
        if not нужно:
            return
        prefix = "" if current.endswith("\n") or not current else "\n"
        exclude.write_text(current + prefix + "\n".join(нужно) + "\n", encoding="utf-8")
    except (RuntimeError, OSError):
        pass


def head(workspace: str | Path) -> str:
    """Текущий коммит; пустая строка — в репозитории ещё нет ни одного.

    Свежий `git init` — обычное начало проекта, и раньше он валил всю реплику:
    `rev-parse HEAD` там падает, а исключение улетало мимо обработчика ошибок,
    и в ухо не приходило вообще ничего.
    """
    return _git(Path(workspace), "rev-parse", "--verify", "--quiet", "HEAD", check=False)


def has_changes(workspace: str | Path) -> bool:
    return bool(_git(Path(workspace), "status", "--porcelain", "--", ".", *OURS))


def commit_all(workspace: str | Path, message: str) -> str | None:
    """Складывает всё изменённое в один коммит. None — менять было нечего."""
    path = Path(workspace)
    if not has_changes(path):
        return None
    # Без pathspec: `git add` с отрицательным pathspec падает целиком, если
    # названный в нём каталог вдобавок лежит в списке игнорируемых, — а мы сами
    # кладём туда `.voice-shell/`. На живом прогоне это значило, что первая
    # правка коммитилась, а все следующие — уже нет, и «откати последнее»
    # отвечало «я ничего не менял» после того, как Claude переписал три файла.
    _git(path, "add", "-A", "--", ".")
    _git(path, "reset", "--quiet", "--", STATE_DIR, check=False)
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


# Обращение и слова-затравки в начале — часть команды, а не её отмена.
OPENERS = ("клод", "клауд", "слушай", "эй", "окей", "ок", "а", "ну", "и", "так",
           "давай", "пожалуйста")


def matches(text: str, phrases: tuple[str, ...]) -> bool:
    """Команда ли это оболочке.

    Раньше искалась подстрока по всей реплике, и вопрос «а это можно
    откатить?» делал настоящий откат. Команда должна стоять в начале — после
    обращения, но не после рассуждения о ней.
    """
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    while words and words[0] in OPENERS:
        words.pop(0)
    lowered = " ".join(words)
    return any(lowered == phrase or lowered.startswith(phrase + " ") for phrase in phrases)
