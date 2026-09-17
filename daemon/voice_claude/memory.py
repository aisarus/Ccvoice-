"""Долговременная память.

Сессия Claude живёт долго, но не вечно: перезапуск демона — и он снова не знает,
как тебя зовут, как ты называешь проекты и на чём вы уже обжигались. Память —
обычный текстовый файл, который подкладывается в каждую реплику и который
человек пополняет голосом.

Файл открыт и редактируем руками: то, что агент помнит о тебе, не должно быть
спрятано в бинарном хранилище.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

REMEMBER_PHRASES = ("запомни что", "запомни", "имей в виду что", "имей в виду",
                    "на будущее", "не забудь что", "не забудь")
RECALL_PHRASES = ("что ты обо мне помнишь", "что ты помнишь", "покажи память",
                  "что у тебя в памяти")
FORGET_PHRASES = ("забудь про", "забудь что", "забудь")

HEADER = "# Память Voice Shell\n\nЧто Claude знает о хозяине и его проектах.\n"


class Memory:
    def __init__(self, workspace: str | Path, path: str | Path | None = None) -> None:
        env_path = os.environ.get("VOICE_MEMORY")
        self.path = Path(path or env_path or Path(workspace) / ".voice-shell" / "memory.md")

    # -- чтение -----------------------------------------------------------
    def text(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def facts(self) -> list[str]:
        """Сами факты, без служебной даты в конце строки."""
        found = []
        for line in self.text().splitlines():
            if not line.startswith("- "):
                continue
            fact = re.sub(r"\s*<!--.*?-->\s*$", "", line[2:]).strip()
            if fact:
                found.append(fact)
        return found

    def hint(self, limit: int = 40) -> str:
        facts = self.facts()[-limit:]
        if not facts:
            return ""
        return "[память] " + " · ".join(facts)

    # -- запись -----------------------------------------------------------
    def remember(self, fact: str) -> str:
        """Добавляет факт. Возвращает то, что записалось."""
        fact = fact.strip().rstrip(".")
        if not fact:
            return ""
        if fact.lower() in {existing.lower() for existing in self.facts()}:
            return fact
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self.path.exists():
                self.path.write_text(HEADER, encoding="utf-8")
            stamp = time.strftime("%Y-%m-%d")
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(f"- {fact}  <!-- {stamp} -->\n")
        except OSError:
            return ""
        return fact

    def forget(self, needle: str) -> int:
        """Убирает факты, где встречается сказанное. Возвращает сколько убрал."""
        words = _normalise(needle).split()
        if not words:
            return 0
        lines = self.text().splitlines()
        kept = [line for line in lines
                if not (line.startswith("- ") and _mentions(line, words))]
        removed = len(lines) - len(kept)
        if removed:
            try:
                self.path.write_text("\n".join(kept) + "\n", encoding="utf-8")
            except OSError:
                return 0
        return removed


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


# Русские окончания: всё, чем слово вправе отличаться от того же слова в
# другом падеже. Список нужен, чтобы «проектор» не считался формой «проекта»:
# без него хватило бы общего начала, и «забудь про проектор» унесло бы факт
# про проект.
ENDINGS = frozenset((
    "", "а", "е", "и", "й", "о", "у", "ы", "ь", "ю", "я",
    "ам", "ах", "ев", "ей", "ем", "ии", "им", "ов", "ом", "ою", "ую", "ые",
    "ый", "ым", "ых", "ье", "ья", "ям", "ях", "ем", "ie",
    "ами", "ями", "его", "ему", "ими", "ого", "ому", "ыми", "ьях", "ьям",
))


def _same_word(needle: str, word: str, least: int = 3) -> bool:
    """Одно ли это слово в разных падежах.

    «Забудь про ночи» не убирало факт «работаю по ночам»: искалось буквальное
    совпадение. Полную морфологию сюда тащить незачем — у одного слова общее
    начало и хвосты, которые выглядят как окончания.
    """
    if needle == word:
        return True
    common = 0
    for left, right in zip(needle, word):
        if left != right:
            break
        common += 1
    if common < least:
        return False
    return needle[common:] in ENDINGS and word[common:] in ENDINGS


def _mentions(line: str, words: list[str]) -> bool:
    """Все ли названные слова есть в строке — пусть и в другом падеже."""
    haystack = _normalise(line).split()
    return all(any(_same_word(word, other) for other in haystack) for word in words)


def _after(text: str, phrases: tuple[str, ...]) -> str | None:
    """Хвост после команды: «запомни что я работаю по ночам» -> «я работаю по ночам»."""
    lowered = _normalise(text)
    for phrase in phrases:
        if lowered == phrase:
            return ""
        if lowered.startswith(phrase + " "):
            return text.strip()[len(phrase):].strip(" ,.:—-").strip()
    return None


def remember_intent(text: str) -> str | None:
    return _after(text, REMEMBER_PHRASES)


def forget_intent(text: str) -> str | None:
    return _after(text, FORGET_PHRASES)


def recall_intent(text: str) -> bool:
    lowered = _normalise(text)
    return any(lowered == phrase or lowered.startswith(phrase) for phrase in RECALL_PHRASES)
