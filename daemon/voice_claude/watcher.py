"""Проактивность: заметить самому и сказать.

Ассистент, который говорит только когда его спросили, — это командная строка с
микрофоном. Наблюдатель смотрит на внешние события (пока — за сборками на
GitHub) и заговаривает первым.

Три правила, без которых проактивность превращается в назойливость: не чаще
заданного, не ночью и не про то, о чём уже сказал.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .i18n import t

MODES = ("off", "watch", "fix")


@dataclass
class Event:
    key: str
    text: str
    kind: str = "ci_failed"
    fix_prompt: str = ""


def mode() -> str:
    value = os.environ.get("PROACTIVE", "watch").strip().lower()
    return value if value in MODES else "watch"


def quiet_hours() -> tuple[int, int] | None:
    """«23-8» — молчать с 23:00 до 8:00. Пусто или off — не молчать никогда."""
    raw = os.environ.get("QUIET_HOURS", "23-8").strip().lower()
    if raw in ("", "off", "none"):
        return None
    try:
        start, end = (int(part) for part in raw.split("-", 1))
    except ValueError:
        return 23, 8
    return start % 24, end % 24


def is_quiet(now: time.struct_time | None = None, hours: tuple[int, int] | None = None) -> bool:
    window = hours if hours is not None else quiet_hours()
    if window is None:
        return False
    hour = (now or time.localtime()).tm_hour
    start, end = window
    return start <= hour or hour < end if start > end else start <= hour < end


def github_failures(workspace: str | Path, limit: int = 5) -> list[Event]:
    """Упавшие сборки, о которых стоит сказать."""
    try:
        result = subprocess.run(
            ["gh", "run", "list", "--limit", str(limit), "--json",
             "databaseId,status,conclusion,headBranch,workflowName,displayTitle"],
            cwd=str(workspace), capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        runs = json.loads(result.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []

    events = []
    for run in runs:
        if run.get("status") != "completed" or run.get("conclusion") != "failure":
            continue
        name = run.get("workflowName") or t("watch.build_default_name")
        branch = run.get("headBranch") or ""
        events.append(Event(
            key=f"run-{run.get('databaseId')}",
            text=(t("watch.build_failed_branch", name=name, branch=branch) if branch
                  else t("watch.build_failed", name=name)),
            fix_prompt=t("watch.fix_prompt", name=name, branch=branch),
        ))
    return events


class Watcher:
    """Решает, о чём и когда говорить. Сам опрос подменяется в тестах."""

    def __init__(self, workspace: str | Path, poll: Callable[[], list[Event]] | None = None,
                 max_per_window: int = 2, window_s: int = 300, min_gap_s: int = 60) -> None:
        self.workspace = Path(workspace)
        self.poll = poll or (lambda: github_failures(self.workspace))
        self.max_per_window, self.window_s, self.min_gap_s = max_per_window, window_s, min_gap_s
        self._spoken: set[str] = set()
        self._times: list[float] = []

    def _allowed(self, now: float) -> bool:
        self._times = [t for t in self._times if now - t <= self.window_s]
        if self._times and now - self._times[-1] < self.min_gap_s:
            return False
        return len(self._times) < self.max_per_window

    def check(self, now: float | None = None, clock: time.struct_time | None = None) -> list[Event]:
        """Что сказать прямо сейчас. Пустой список — молчим."""
        if mode() == "off" or is_quiet(clock):
            return []
        now = now if now is not None else time.monotonic()
        fresh = [event for event in self.poll() if event.key not in self._spoken]
        speak: list[Event] = []
        for event in fresh:
            self._spoken.add(event.key)          # не повторяемся даже если сейчас молчим
            if self._allowed(now):
                self._times.append(now)
                speak.append(event)
        return speak
