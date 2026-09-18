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

from . import lexicon
from .i18n import join, t
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
        return t("fmt.fixed_one", first=stems[0])
    if len(stems) <= 3:
        names = f"{join(stems[:-1])} {t('fmt.and')} {stems[-1]}"
        return t("fmt.fixed_list", names=names)
    return t("fmt.fixed_more", first=stems[0], second=stems[1], rest=len(stems) - 2)


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
        parts.append(t("fmt.tests_failed", count=failed.group(1)))
    elif passed:
        parts.append(t("fmt.tests_passed", count=passed.group(1)))

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
    normalise = lexicon.normalise
    lowered = normalise(text)
    if not lowered:
        return None
    best: tuple[int, str] | None = None
    for rule in section("permissions")["answers"]:
        for utterance in rule["utterances"]:
            u = normalise(utterance)
            matched = (lowered == u or lowered.startswith(u + " ")
                       or f" {u} " in f" {lowered} "
                       or (lexicon.CJK.search(u) and u in lowered))
            # Самое длинное совпадение выигрывает: "да, и больше не спрашивай"
            # не должно превратиться в простое "да".
            if matched and (best is None or len(u) > best[0]):
                best = (len(u), rule["action"])
    return best[1] if best else None


def approval_to_speech(raw: str) -> str:
    """Turn a tool-permission request into a human question."""
    tool = raw.strip().splitlines()[0][:120]
    human = {
        r"\brm\b.*build": "fmt.tool.rm_build",
        r"\bnpm install\b": "fmt.tool.npm_install",
        r"\bgit push\b": "fmt.tool.git_push",
        r"\bgit commit\b": "fmt.tool.git_commit",
        r"\bpytest\b|\bnpm test\b": "fmt.tool.tests",
    }
    for pattern, key in human.items():
        if re.search(pattern, tool, re.I):
            return t("fmt.permission_known", phrase=t(key))
    return t("fmt.permission_tool", tool=tool)


# Технический текст ошибки в ухо не годится: там стек, коды и пути. Но и
# «что-то пошло не так» бесполезно — человек должен понять, чинить ли ему
# что-то самому. Частные причины идут первыми: «сессия не поднялась» написано
# на любой поломке CLI и перебивало собой и кончившийся лимит, и полный диск.
FAILURE_HINTS = (
    (r"root/sudo privileges", "reason.root"),
    (r"credit|quota|rate.?limit|too low", "reason.quota"),
    (r"overloaded|529\b|503\b", "reason.overloaded"),
    (r"No space left|ENOSPC|disk quota", "reason.disk"),
    (r"ENOTFOUND|ECONNREFUSED|Temporary failure|getaddrinfo|fetch failed|"
     r"EAI_AGAIN|ENETUNREACH|socket hang up", "reason.network"),
    (r"not a git repository|Not a git repo", "reason.not_repo"),
    (r"timed? ?out|ETIMEDOUT", "reason.timeout"),
    (r"permission denied|EACCES", "reason.permission"),
    (r"not found|No such file|ENOENT", "reason.not_found"),
    (r"exit code 1\b|Command failed|exit code: 1\b", "reason.session"),
)

# То же самое, но по структуре, а не по словам: SDK кладёт причину отказа
# в поля, и угадывать её по тексту незачем.
API_STATUS_HINTS = {
    401: "reason.token_rejected", 403: "reason.token_rejected",
    429: "reason.quota", 500: "reason.overloaded",
    503: "reason.overloaded", 529: "reason.overloaded",
}


def reason_for_voice(exc: BaseException) -> str:
    """Одна короткая фраза о причине — без стека, кодов и путей."""
    status = getattr(exc, "api_error_status", None)
    if isinstance(status, int):
        return _sentence(t(API_STATUS_HINTS.get(status) or "reason.api_error"))
    if getattr(exc, "subtype", None) == "error_max_turns":
        return t("reason.max_turns")
    text = f"{type(exc).__name__}: {exc}"
    for pattern, key in FAILURE_HINTS:
        if re.search(pattern, text, re.I):
            return _sentence(t(key))
    return t("reason.in_log")


def _sentence(phrase: str) -> str:
    """Причина — это кусок фразы; вслух она идёт отдельным предложением.

    Точка и заглавная ставятся только там, где они существуют: в китайском
    заглавных букв нет, а точка своя.
    """
    if lexicon.CJK.search(phrase):
        return phrase if phrase.endswith(("。", "！", "？")) else phrase + "。"
    return phrase[:1].upper() + phrase[1:] + "."
