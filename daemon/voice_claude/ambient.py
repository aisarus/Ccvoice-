"""Ambient mode: the "second ear".

Implements spec section `ambient_mode`. A bounded ring of transcript lines with
speaker roles, no raw audio, wiped on exit. In `passive` nothing leaves the
device until a recall question is asked; `assist` additionally streams to the
chat target under trigger and rate limits.
"""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable

from .i18n import t
from .spec import defaults, section

RECALL_PATTERNS = (
    r"что (он|она|они|ты)?\s*\S*\s*(сказал|говорил|назвал)",
    r"повтори (последнее|что было)",
    r"как(ую|ое|ая)? (цифр|числ|сумм|дат)",
    r"как (его|её|ее|их) зовут",
    r"о ч(ё|е)м (мы|шла речь|говорили|договорились)",
    r"что (только что|сейчас) (было|прозвучало)",
)


@dataclass
class Line:
    ts: float
    role: str
    text: str
    confidence: float

    def rendered(self) -> str:
        who = {"master": t("ambient.me"),
               "bystander": t("ambient.bystander"), "unknown": "?"}[self.role]
        return f"[{time.strftime('%H:%M', time.localtime(self.ts))}] {who}: {self.text}"


class AmbientBuffer:
    """Bounded, role-tagged, audio-free, wipeable."""

    def __init__(self, submode: str | None = None, config: dict[str, Any] | None = None) -> None:
        self._spec = section("ambient_mode")
        self._cfg = dict(defaults("ambient"))
        if config:
            self._cfg.update(config)
        self._submodes = {m["id"] for m in self._spec["submodes"]}
        self.submode = submode or self._cfg["submode"]
        self._lines: deque[Line] = deque()

    @property
    def enabled(self) -> bool:
        return self.submode != "off"

    @property
    def retention_s(self) -> float:
        return float(self._cfg["buffer_min"]) * 60.0

    @property
    def bystander_transcript(self) -> bool:
        return bool(self._cfg["bystander_transcript"])

    def set_bystander_transcript(self, enabled: bool) -> None:
        """Явный опт-ин: чужая речь по умолчанию не попадает в буфер."""
        if not enabled:
            self._lines = deque(line for line in self._lines if line.role == "master")
        self._cfg["bystander_transcript"] = bool(enabled)

    def set_submode(self, submode: str) -> None:
        if submode not in self._submodes:
            raise ValueError(f"unknown ambient submode {submode!r}")
        if submode == "off":
            self.wipe()
        self.submode = submode

    def add(self, role: str, text: str, confidence: float = 1.0, now: float | None = None) -> bool:
        """Returns True if the line was kept. Bystanders are dropped by default."""
        if not self.enabled or not text.strip():
            return False
        if role == "self_echo":
            return False
        if role != "master" and not self._cfg["bystander_transcript"]:
            return False
        now = now if now is not None else time.time()
        self._lines.append(Line(now, role, text.strip(), confidence))
        self._expire(now)
        return True

    def _expire(self, now: float) -> None:
        cutoff = now - self.retention_s
        while self._lines and self._lines[0].ts < cutoff:
            self._lines.popleft()

    def wipe(self) -> None:
        self._lines.clear()

    def lines(self, now: float | None = None) -> list[Line]:
        self._expire(now if now is not None else time.time())
        return list(self._lines)

    def transcript(self, now: float | None = None) -> str:
        return "\n".join(line.rendered() for line in self.lines(now))

    def leaves_device(self) -> bool:
        """passive keeps everything local; only assist mirrors to the daemon."""
        return self.submode == "assist"

    @staticmethod
    def is_recall(text: str) -> bool:
        lowered = re.sub(r"[^\w\s]", " ", text.lower())
        return any(re.search(pattern, lowered) for pattern in RECALL_PATTERNS)


class WhisperGate:
    """Short answers, only into a silence gap, never over the master."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._spec = section("ambient_mode")["whisper_output"]
        cfg = dict(defaults("ambient"))
        if config:
            cfg.update(config)
        self.max_words = int(cfg["whisper_max_words"])
        self.min_gap_ms = int(self._spec["speak_only_in_silence_gap_ms"])

    def may_speak(self, *, master_speaking: bool, silence_ms: int) -> bool:
        if master_speaking:
            return False
        return silence_ms >= self.min_gap_ms

    def shorten(self, text: str) -> str:
        words = text.split()
        if len(words) <= self.max_words:
            return text.strip()
        return " ".join(words[: self.max_words]).rstrip(",.;:") + "."


class RateLimiter:
    def __init__(self, max_per_window: int = 2, window_s: int = 300, min_gap_s: int = 60) -> None:
        self.max_per_window, self.window_s, self.min_gap_s = max_per_window, window_s, min_gap_s
        self._events: deque[float] = deque()

    def allow(self, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        while self._events and now - self._events[0] > self.window_s:
            self._events.popleft()
        if self._events and now - self._events[-1] < self.min_gap_s:
            return False
        if len(self._events) >= self.max_per_window:
            return False
        self._events.append(now)
        return True


def proactive_triggers() -> Iterable[str]:
    return [t["id"] for t in section("ambient_mode")["proactive"]["triggers"]]
