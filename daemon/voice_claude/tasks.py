"""Очередь фоновых задач.

Смысл всей затеи: сказал и забыл. Человек произносит задачу, кладёт телефон
и идёт заниматься своим, а через двадцать минут слышит в ухе итог. Пока
сессия одна, так не выходит: вторая задача молча ждёт первую, а рабочая копия
у них общая — две задачи подрались бы за одни и те же файлы.

Поэтому у каждой задачи своя ветка и свой git worktree: они не видят правок
друг друга, и откат у каждой свой. Само состояние лежит рядом с журналом
точек отката, обычным json, и переживает перезапуск службы — иначе перезапуск
означал бы потерю всего, что человек уже сказал.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

STATE_DIR = ".voice-shell"
WORK_DIR = "work"
FILE = "tasks.json"

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
STUCK = "stuck"
CANCELLED = "cancelled"

# Больше двух сразу — это чужая машина, чужие деньги и общий git.
DEFAULT_PARALLEL = 2

RU = {
    QUEUED: "ждёт",
    RUNNING: "в работе",
    DONE: "готова",
    STUCK: "встала",
    CANCELLED: "отменена",
}


@dataclass
class Task:
    id: str
    text: str
    state: str = QUEUED
    branch: str = ""
    worktree: str = ""
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0
    summary: str = ""
    delivered: bool = False

    @property
    def title(self) -> str:
        text = self.text.strip()
        return text if len(text) <= 60 else text[:57] + "…"

    @property
    def spoken_state(self) -> str:
        return RU.get(self.state, self.state)

    def minutes(self) -> int:
        end = self.finished or time.time()
        return max(0, int((end - (self.started or self.created)) // 60))


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, timeout=60)
    if check and done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"git {' '.join(args)}")
    return done.stdout.strip()


def _slug(text: str) -> str:
    """Кусок реплики в имя ветки: человеку потом читать этот список."""
    bare = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.U)
    words = [w for w in bare.split() if len(w) > 2][:3]
    slug = "-".join(words) or "задача"
    return slug[:40]


class TaskQueue:
    """Список задач и их рабочие копии. Ничего не запускает сама."""

    def __init__(self, workspace: str | Path, parallel: int = DEFAULT_PARALLEL) -> None:
        self.workspace = Path(workspace).expanduser()
        self.parallel = max(1, parallel)
        self.path = self.workspace / STATE_DIR / FILE
        self.tasks: list[Task] = []
        self._load()

    # -- хранение --------------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in raw:
            try:
                self.tasks.append(Task(**item))
            except TypeError:
                continue
        # Служба падала посреди работы — задача не «в работе», она встала.
        for task in self.tasks:
            if task.state == RUNNING:
                task.state = STUCK
                task.summary = task.summary or "прервалась при перезапуске службы"

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps([asdict(t) for t in self.tasks], ensure_ascii=False, indent=1),
                encoding="utf-8")
        except OSError:
            pass

    # -- очередь ---------------------------------------------------------
    def add(self, text: str) -> Task:
        number = len(self.tasks) + 1
        task = Task(id=f"t{number}", text=text.strip())
        self.tasks.append(task)
        self.save()
        return task

    def get(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def find(self, words: str) -> Task | None:
        """«Отмени задачу про тесты» — ищем по словам реплики, не по номеру."""
        needle = [w for w in re.split(r"\W+", words.lower(), flags=re.U) if len(w) > 3]
        if not needle:
            return None
        best: tuple[int, Task] | None = None
        for task in self.tasks:
            hay = task.text.lower()
            score = sum(1 for w in needle if w in hay)
            if score and (best is None or score > best[0]):
                best = (score, task)
        return best[1] if best else None

    def by_state(self, *states: str) -> list[Task]:
        return [t for t in self.tasks if t.state in states]

    @property
    def running(self) -> list[Task]:
        return self.by_state(RUNNING)

    def next_to_start(self) -> Task | None:
        """Кого запускать следующим — с оглядкой на потолок параллельности."""
        if len(self.running) >= self.parallel:
            return None
        return next((t for t in self.tasks if t.state == QUEUED), None)

    # -- рабочие копии ---------------------------------------------------
    def start(self, task: Task) -> Task:
        """Заводит ветку и отдельную копию, чтобы задачи не мешали друг другу."""
        task.branch = f"voice/{task.id}-{_slug(task.text)}"
        target = self.workspace / STATE_DIR / WORK_DIR / task.id
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            _git(self.workspace, "worktree", "add", "-b", task.branch, str(target), "HEAD")
        task.worktree = str(target)
        task.state = RUNNING
        task.started = time.time()
        self.save()
        return task

    def finish(self, task: Task, summary: str, stuck: bool = False) -> Task:
        task.state = STUCK if stuck else DONE
        task.summary = summary.strip()
        task.finished = time.time()
        self.save()
        return task

    def cancel(self, task: Task) -> Task:
        task.state = CANCELLED
        task.finished = time.time()
        self.save()
        return task

    def undeliverable(self) -> list[Task]:
        """Готовые, о которых ещё не сказали вслух."""
        return [t for t in self.tasks if t.state in (DONE, STUCK) and not t.delivered]

    def mark_delivered(self, tasks: Iterable[Task]) -> None:
        for task in tasks:
            task.delivered = True
        self.save()

    def drop_worktree(self, task: Task) -> None:
        """Убрать копию, когда она больше не нужна. Ветку оставляем."""
        if not task.worktree:
            return
        try:
            _git(self.workspace, "worktree", "remove", "--force", task.worktree)
        except (RuntimeError, OSError):
            pass
        task.worktree = ""
        self.save()

    # -- разговор о задачах ----------------------------------------------
    def spoken_status(self) -> str:
        running, queued = self.running, self.by_state(QUEUED)
        if not running and not queued:
            ready = self.by_state(DONE)
            return "Ничего не делаю." if not ready else f"Всё сделано, готовых задач {len(ready)}."
        parts = []
        if running:
            first = running[0]
            parts.append(f"Делаю: {first.title}, уже {first.minutes()} минут")
            if len(running) > 1:
                parts.append(f"и ещё {len(running) - 1}")
        if queued:
            parts.append(f"в очереди {len(queued)}")
        return ", ".join(parts) + "."


def state_dir_of(workspace: str | Path) -> Path:
    return Path(workspace).expanduser() / STATE_DIR

# -- как об этом говорят вслух ---------------------------------------------
#
# Фразы разбираются здесь, а не в демоне: тогда их видно рядом с самой
# очередью и можно проверить тестом, не поднимая ни сокета, ни Claude.

_BACKGROUND = (
    "в фоне", "фоном", "займись", "потом сделай", "сделай потом",
    "поставь в очередь", "добавь задачу", "на потом",
)
_STATUS = (
    "чем занят", "чем занимаешься", "что в работе", "что делаешь сейчас",
    "какие задачи", "что в очереди", "статус задач",
)
_READY = ("что готово", "покажи готовое", "что доделал", "какие задачи готовы")
_CANCEL = ("отмени задачу", "брось задачу", "убери задачу", "не делай задачу")


def _has(text: str, phrases: Iterable[str]) -> str | None:
    lowered = " ".join(text.lower().split())
    for phrase in phrases:
        if phrase in lowered:
            return phrase
    return None


def background_request(text: str) -> str | None:
    """«Клод, в фоне почини тесты» — вернёт саму задачу без служебных слов.

    Фоновой задачу делает не длительность, а решение человека не ждать
    ответа. Поэтому спрашиваем не эвристику, а его самого.
    """
    phrase = _has(text, _BACKGROUND)
    if phrase is None:
        return None
    lowered = " ".join(text.lower().split())
    rest = lowered.replace(phrase, " ", 1)
    rest = re.sub(r"^[\s,.:—-]+", "", rest).strip()
    return rest or None


def is_status_question(text: str) -> bool:
    return _has(text, _STATUS) is not None


def is_ready_question(text: str) -> bool:
    return _has(text, _READY) is not None


def cancel_request(text: str) -> str | None:
    """«Отмени задачу про зависимости» — вернёт слова, по которым искать."""
    phrase = _has(text, _CANCEL)
    if phrase is None:
        return None
    lowered = " ".join(text.lower().split())
    rest = lowered.split(phrase, 1)[1]
    return re.sub(r"^[\s,.:—-]*(про|о|об)\s+", "", rest).strip() or None
