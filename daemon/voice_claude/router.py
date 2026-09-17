"""Routing between targets: code / chat / note.

Implements spec section `targets.router`. Resolution order is explicit prefix,
sticky target inside the conversation window, classifier, default. The default
is `chat` on purpose: an ambiguous utterance must never execute anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .spec import defaults, section

FILE_RE = re.compile(r"\b[\w./-]+\.(ts|tsx|js|jsx|py|json|md|yml|yaml|toml|rs|go|sh)\b", re.I)
CODE_LEXICON = (
    "git", "коммит", "закоммить", "коммить", "ветк", "мердж", "пул реквест", "pr",
    "тест", "npm", "pytest", "билд", "сборк", "деплой", "deployment", "линт",
    "рефактор", "запусти", "исправ", "почини", "откати", "репозитор", "функци",
    "баг", "ошибк", "стек", "компилир", "docker", "миграц",
)
CHAT_LEXICON = (
    "что такое", "кто такой", "объясни", "посчитай", "сформулируй", "напиши письмо",
    "как думаешь", "переведи", "что он сказал", "что она сказала", "напомни",
    "во сколько", "сколько будет", "какая разница", "стоит ли",
)
CONTINUATION = ("продолжай", "добей", "давай", "дальше", "ок делай", "окей делай")


@dataclass
class Route:
    target: str
    reason: str
    confidence: float
    text: str


class Router:
    def __init__(self, spec_section: dict[str, Any] | None = None,
                 config: dict[str, Any] | None = None) -> None:
        self._spec = spec_section or section("targets")
        self._cfg = config or defaults("targets")
        self._router = self._spec["router"]
        self._targets = {t["id"]: t for t in self._spec["list"]}
        self.sticky_target: str | None = None
        # Явный выбор пользователя в интерфейсе: держится, пока его не снимут,
        # в отличие от sticky-цели, которая живёт только внутри окна диалога.
        self.forced_target: str | None = None

    @property
    def default_target(self) -> str:
        return self._router["default_target"]

    def route(self, text: str, *, ms_since_last: int = 10 ** 6,
              sticky_target: str | None = None,
              learned: "tuple[str, float] | None" = None) -> Route:
        cleaned = text.strip()
        lowered = cleaned.lower()

        prefix = self._match_prefix(lowered)
        if prefix is not None:
            target, stripped = prefix
            self.sticky_target = target
            return Route(target, "explicit_prefix", 1.0, stripped or cleaned)

        if self.forced_target:
            return Route(self.forced_target, "forced", 1.0, cleaned)

        # Прошлая поправка человека весит больше, чем угадывание по словам.
        if learned and learned[0] in self._targets:
            return Route(learned[0], "learned", learned[1], cleaned)

        sticky = sticky_target if sticky_target is not None else self.sticky_target
        window_ms = self._router["sticky_target"]["window_s"] * 1000
        if sticky and ms_since_last <= window_ms:
            return Route(sticky, "sticky", 0.9, cleaned)

        target, confidence = self._classify(lowered)
        if target is not None:
            self.sticky_target = target
            return Route(target, "classifier", confidence, cleaned)

        self.sticky_target = self.default_target
        return Route(self.default_target, "default", 0.5, cleaned)

    # -- internals -------------------------------------------------------
    def _match_prefix(self, lowered: str) -> tuple[str, str] | None:
        best: tuple[str, str] | None = None
        best_len = 0
        for rule in self._router["explicit_prefix"]:
            for utterance in rule["utterances"]:
                u = utterance.lower()
                if lowered.startswith(u) and len(u) > best_len:
                    rest = lowered[len(u):].lstrip(" ,.:—-")
                    best, best_len = (rule["target"], rest), len(u)
        return best

    def _classify(self, lowered: str) -> tuple[str | None, float]:
        code_hits = sum(1 for token in CODE_LEXICON if token in lowered)
        code_hits += 2 * len(FILE_RE.findall(lowered))
        chat_hits = sum(1 for token in CHAT_LEXICON if token in lowered)

        if any(lowered.startswith(word) for word in CONTINUATION) and self.sticky_target:
            return self.sticky_target, 0.8

        if code_hits and code_hits > chat_hits:
            # Normalised so a single weak keyword cannot reach the code gate.
            confidence = min(0.95, 0.55 + 0.15 * code_hits)
            if confidence >= self._cfg["min_confidence_for_code"]:
                return "code", confidence
            return "chat", 1.0 - confidence
        if chat_hits:
            return "chat", min(0.95, 0.6 + 0.1 * chat_hits)
        return None, 0.0

    def force(self, target: str | None) -> None:
        """Чип в интерфейсе: None возвращает к автоматическому выбору."""
        if target is not None and target not in self._targets:
            raise ValueError(f"unknown target {target!r}")
        self.forced_target = target
        self.sticky_target = target

    def handoff(self, text: str) -> str | None:
        """Detect a cross-target handoff phrase, return the phrase id."""
        lowered = text.lower()
        for rule in self._router["handoff"]:
            if any(u in lowered for u in rule["utterances"]):
                return rule["effect"]
        return None

    def is_misroute_recovery(self, text: str) -> bool:
        lowered = text.lower()
        return any(u in lowered for u in self._router["misroute_recovery"]["utterances"])

    def other_target(self, target: str | None) -> str:
        """Куда переслать после «не туда»: обычно путаются код и чат."""
        return {"code": "chat", "chat": "code", "note": "chat"}.get(target or "", "code")

    def earcon_for(self, target: str) -> str | None:
        return {"code": "to_code", "chat": "to_chat"}.get(target)

    def is_mutating(self, target: str) -> bool:
        return bool(self._targets[target]["mutating"])
