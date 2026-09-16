"""Voice formatter: Claude's output -> 1-3 spoken sentences.

Implements spec section `voice_formatter`. The full output stays on screen;
only the summary is spoken. The rule-based path here is the fallback that works
offline and within the latency budget; `summarize` takes an optional LLM
callable for the preferred path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .spec import section

FENCE_RE = re.compile(r"```.*?```", re.S)
COMMAND_RE = re.compile(
    r"^\s*(?:[$>#]|(?:sudo|npm|npx|yarn|pnpm|pip|python|pytest|git|docker|make|cd|ls|cat|"
    r"grep|rm|mv|cp|curl|node|go|cargo|bash|sh)\b)", re.I)
TESTS_RE = re.compile(r"(\d+)\s+(?:tests?|тест\w*)\s+(passed|passing|проход\w*)", re.I)
FAILED_RE = re.compile(r"(\d+)\s+(?:tests?|тест\w*)\s+(failed|провал\w*|падают?)", re.I)
MODIFIED_RE = re.compile(r"^\s*(?:modified|changed|изменено|обновлено)\s*:?\s*$", re.I)
PATH_RE = re.compile(r"^\s*([\w./-]+\.\w{1,5})\s*$")
QUESTION_RE = re.compile(r"[^.!?\n]*\?\s*$", re.M)


@dataclass
class VoiceSummary:
    text: str
    is_question: bool
    sentences: int

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.text


def _stem(path: str) -> str:
    return re.sub(r"\.\w+$", "", path.rsplit("/", 1)[-1])


def _touched_files(output: str) -> list[str]:
    files, collecting = [], False
    for line in output.splitlines():
        if MODIFIED_RE.match(line):
            collecting = True
            continue
        match = PATH_RE.match(line)
        if collecting and match:
            files.append(match.group(1))
        elif collecting and line.strip() == "":
            collecting = False
    return files


def _spoken_files(files: list[str]) -> str:
    stems, seen = [], set()
    for path in files:
        if "test" in path.lower():
            continue
        stem = _stem(path)
        if stem not in seen:
            seen.add(stem)
            stems.append(stem)
    if not stems:
        return ""
    if len(stems) == 1:
        return f"Исправил {stems[0]}."
    if len(stems) <= 3:
        return "Исправил " + " и ".join([", ".join(stems[:-1]), stems[-1]]) + "."
    return f"Исправил {stems[0]}, {stems[1]} и ещё {len(stems) - 2} файла."


def summarize(output: str, llm: Callable[[str], str] | None = None,
              max_sentences: int | None = None) -> VoiceSummary:
    """Rule-based summary; `llm` overrides it when the daemon can afford it."""
    limit = max_sentences or section("config_defaults")["tts"]["max_sentences"]

    if llm is not None:
        spoken = _trim_to(llm(output).strip(), limit)
        return VoiceSummary(spoken, spoken.rstrip().endswith("?"), _count_sentences(spoken))

    output = FENCE_RE.sub(" ", output)          # блоки кода вслух не читаются
    parts: list[str] = []
    files = _touched_files(output)
    if files:
        parts.append(_spoken_files(files))

    failed = FAILED_RE.search(output)
    passed = TESTS_RE.search(output)
    if failed:
        parts.append(f"{failed.group(1)} тестов падают.")
    elif passed:
        parts.append(f"Все {passed.group(1)} тестов проходят.")

    question = _last_question(output)
    if question:
        parts.append(question)

    if not parts:
        parts.append(_first_sentences(output, limit))

    text = " ".join(p for p in parts if p).strip()
    text = _trim_to(text, limit)
    return VoiceSummary(text, text.rstrip().endswith("?"), _count_sentences(text))


def _last_question(output: str) -> str:
    questions = [q.strip() for q in QUESTION_RE.findall(output) if len(q.strip()) > 3]
    return questions[-1] if questions else ""


def _first_sentences(output: str, limit: int) -> str:
    """Только проза: команды, пути и таблицы вслух не читаются."""
    prose = [line.strip() for line in output.splitlines()
             if line.strip()
             and not PATH_RE.match(line)
             and not COMMAND_RE.match(line)
             and not line.lstrip().startswith(("|", "```", "---"))]
    return _trim_to(" ".join(prose), limit)


def _count_sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


def _trim_to(text: str, limit: int) -> str:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    return " ".join(sentences[:limit])


def parse_approval(text: str) -> str | None:
    """Разбирает голосовой ответ на запрос разрешения (спека: permissions.answers)."""
    def normalise(value: str) -> str:
        return " ".join(re.sub(r"[^\w\s]", " ", value.lower()).split())

    lowered = normalise(text)
    if not lowered:
        return None
    best: tuple[int, str] | None = None
    for rule in section("permissions")["answers"]:
        for utterance in rule["utterances"]:
            u = normalise(utterance)
            matched = lowered == u or lowered.startswith(u + " ") or f" {u} " in f" {lowered} "
            # Самое длинное совпадение выигрывает: "да, и больше не спрашивай"
            # не должно превратиться в простое "да".
            if matched and (best is None or len(u) > best[0]):
                best = (len(u), rule["action"])
    return best[1] if best else None


def approval_to_speech(raw: str) -> str:
    """Turn a tool-permission request into a human question."""
    tool = raw.strip().splitlines()[0][:120]
    human = {
        r"\brm\b.*build": "удалить старую папку build",
        r"\bnpm install\b": "установить зависимости",
        r"\bgit push\b": "запушить изменения",
        r"\bgit commit\b": "закоммитить изменения",
        r"\bpytest\b|\bnpm test\b": "запустить тесты",
    }
    for pattern, phrase in human.items():
        if re.search(pattern, tool, re.I):
            return f"Клод хочет {phrase}. Разрешить?"
    return f"Клод хочет выполнить: {tool}. Разрешить?"
