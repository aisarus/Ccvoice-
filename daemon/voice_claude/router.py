"""Routing between targets: code / chat / note.

Implements spec section `targets.router`. Resolution order is explicit prefix,
sticky target inside the conversation window, classifier, default. The default
is `chat` on purpose: an ambiguous utterance must never execute anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import lexicon
from .spec import defaults, section

FILE_RE = re.compile(r"\b[\w./-]+\.(ts|tsx|js|jsx|py|json|md|yml|yaml|toml|rs|go|sh)\b", re.I)
# Признаки цели живут в lexicon на четырёх языках сразу: какой из них
# прозвучит следующим, роутер заранее не знает.
CODE_STEM_RES, CODE_WORD_RES = lexicon.code_patterns()
# Совместимость: прежний плоский словарь, по которому кто-то может пройтись.
CODE_LEXICON = tuple(lexicon.SHARED_CODE_STEMS) + tuple(
    stem for lang in lexicon.LANGUAGES for stem in lexicon.CODE_STEMS.get(lang, ())
) + tuple(word for lang in lexicon.LANGUAGES for word in lexicon.CODE_WORDS.get(lang, ()))
CHAT_LEXICON = lexicon.every("chat")
CONTINUATION = lexicon.every("continuation")


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
        target, confidence = self._classify(lowered)
        if sticky and ms_since_last <= window_ms:
            # Липкость держит короткие продолжения, но целую фразу с явными
            # признаками другой цели перебивать не должна: «что такое вектор
            # эмбеддинга» сразу после работы с кодом — это вопрос.
            yields_at = self._router["sticky_target"].get("yields_to_classifier_at", 1.1)
            if not (target and target != sticky and confidence >= yields_at):
                return Route(sticky, "sticky", 0.9, cleaned)

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
                if self._prefix_fits(lowered, u) and len(u) > best_len:
                    rest = lowered[len(u):].lstrip(" ,.:—-，。、")
                    best, best_len = (rule["target"], rest), len(u)
        return best

    @staticmethod
    def _prefix_fits(lowered: str, prefix: str) -> bool:
        """Префикс — это слово целиком, а не начало другого.

        Без этого «кодекс» уходил в код, а «codebase» — в код на английском:
        `startswith` ничего не знает о границах слов. В китайском границы нет
        вовсе, там достаточно самого начала.
        """
        if not lowered.startswith(prefix):
            return False
        rest = lowered[len(prefix):]
        if not rest or lexicon.CJK.search(prefix[-1:]):
            return True
        return not rest[0].isalnum()

    def _classify(self, lowered: str) -> tuple[str | None, float]:
        code_hits = sum(1 for pattern in CODE_STEM_RES if pattern.search(lowered))
        code_hits += sum(1 for pattern in CODE_WORD_RES if pattern.search(lowered))
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

    # -- маршрут по смыслу ------------------------------------------------
    @property
    def intent_timeout(self) -> float:
        cfg = self._router.get("intent_model") or {}
        return float(cfg.get("timeout_s", 2.5))

    def needs_intent_model(self, route: Route) -> bool:
        """Спрашивать ли модель.

        Только там, где словарь не знает ответа. Произнесённый префикс, чип
        в интерфейсе, прошлая поправка и цель внутри окна диалога — это уже
        решение человека, и переспрашивать его нельзя.
        """
        cfg = self._router.get("intent_model")
        if not cfg or self._cfg.get("intent_model", "auto") != "auto":
            return False
        if route.reason not in ("classifier", "default"):
            return False
        return route.confidence < cfg["min_confidence_to_skip"]

    def apply_intent(self, route: Route, target: str) -> Route:
        """Ответ модели вместо догадки словаря. Чужое слово — игнорируем."""
        if target not in self._targets:
            return route
        self.sticky_target = target
        return Route(target, "intent_model", 0.85, route.text)

    def force(self, target: str | None) -> None:
        """Чип в интерфейсе: None возвращает к автоматическому выбору."""
        if target is not None and target not in self._targets:
            raise ValueError(f"unknown target {target!r}")
        self.forced_target = target
        self.sticky_target = target

    def handoff(self, text: str) -> str | None:
        """Detect a cross-target handoff phrase, return the phrase id."""
        return self._handoff_rule(text)["effect"] if self._handoff_rule(text) else None

    def handoff_target(self, text: str) -> str | None:
        """Куда переезжает разговор вместе с контекстом.

        Цель названа в самой спеке: раньше её приходилось бы угадывать из
        прозы «уходит в code-сессию», а фраза-эффект пишется для человека.
        """
        rule = self._handoff_rule(text)
        target = rule.get("target") if rule else None
        return target if target in self._targets else None

    def _handoff_rule(self, text: str) -> dict[str, Any] | None:
        lowered = text.lower()
        for rule in self._router["handoff"]:
            if any(u in lowered for u in rule["utterances"]):
                return rule
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
