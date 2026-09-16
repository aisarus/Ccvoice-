"""Session state machine and the conversation window.

Implements spec section `states` and `conversation_window`: five states, earcons
instead of spoken status, and a window after each answer during which the wake
word is not needed. Only master speech keeps the window open.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .spec import defaults, section

IDLE, LISTENING, THINKING, SPEAKING, WORKING = "IDLE", "LISTENING", "THINKING", "SPEAKING", "WORKING"


@dataclass
class Machine:
    state: str = IDLE
    window_opened_at: float | None = None
    window_s: float = 0.0
    on_change: Callable[[str, str], None] | None = None

    def __post_init__(self) -> None:
        self._states = set(section("states")["enum"])
        self.window_s = self.window_s or float(defaults("conversation_window_s"))

    def to(self, state: str) -> None:
        if state not in self._states:
            raise ValueError(f"unknown state {state!r}")
        previous, self.state = self.state, state
        if previous != state and self.on_change:
            self.on_change(previous, state)

    # -- conversation window ---------------------------------------------
    def open_window(self, now: float | None = None) -> None:
        self.window_opened_at = now if now is not None else time.monotonic()

    def window_open(self, now: float | None = None) -> bool:
        if self.window_opened_at is None:
            return False
        now = now if now is not None else time.monotonic()
        return (now - self.window_opened_at) <= self.window_s

    def close_window(self) -> None:
        self.window_opened_at = None

    def needs_wake_word(self, now: float | None = None) -> bool:
        return self.state == IDLE and not self.window_open(now)

    def ms_since_window(self, now: float | None = None) -> int:
        if self.window_opened_at is None:
            return 10 ** 6
        now = now if now is not None else time.monotonic()
        return int((now - self.window_opened_at) * 1000)
