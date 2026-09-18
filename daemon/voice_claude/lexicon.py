"""Everything the shell listens *for*, in every language it understands.

The output side (`i18n`) picks one language per utterance. The input side
cannot: the shell has no idea which language the next sentence will be in
until it hears it, and asking people to flip a switch before speaking defeats
the point of a hands-free interface. So command phrases from every language
are matched at once, and only the answer comes back in one.

That is safe because these are commands, not prose: the tables are short,
specific, and chosen not to collide across languages.
"""
from __future__ import annotations

import re

from .i18n import LANGUAGES

CJK = re.compile(r"[一-鿿]")
#: Apostrophes survive normalisation: «don't forget» and «what's done» are
#: command phrases, and stripping the apostrophe turns them into «don t».
PUNCTUATION = re.compile(r"[^\w\s']")


def normalise(text: str) -> str:
    """Lowercase, curly quotes straightened, punctuation gone."""
    return " ".join(PUNCTUATION.sub(" ", text.lower().replace("’", "'")).split())


def _cjk(text: str) -> bool:
    return bool(CJK.search(text))


# --------------------------------------------------------------------------
# Command phrases. Russian entries are the ones in daily use and are kept
# exactly as they were; the rest are translations of the same intent, not
# transliterations of the same words.
# --------------------------------------------------------------------------
PHRASES: dict[str, dict[str, tuple[str, ...]]] = {
    "openers": {
        "en": ("claude", "hey", "ok", "okay", "listen", "so", "well", "please", "yo"),
        "ru": ("клод", "клауд", "слушай", "эй", "окей", "ок", "а", "ну", "и", "так",
               "давай", "пожалуйста"),
        "es": ("claude", "oye", "vale", "bueno", "escucha", "por favor", "venga", "eh"),
        "zh": ("克劳德", "喂", "那个", "请", "帮我"),
    },
    "undo": {
        "en": ("undo the last thing", "undo the last", "undo that", "undo",
               "roll that back", "roll it back", "roll back",
               "revert the last", "revert that", "put it back",
               "undo the changes", "take that back"),
        "ru": ("откати последнее", "откати", "отмени последнее", "отмени изменения",
               "верни как было", "верни обратно", "отмена последнего"),
        "es": ("deshaz lo último", "deshaz eso", "deshaz", "revierte lo último",
               "revierte eso", "revierte", "vuelve atrás", "déjalo como estaba",
               "deshacer"),
        "zh": ("撤销刚才的", "撤销上一步", "撤销", "回滚", "恢复原样", "还原"),
    },
    "history": {
        "en": ("what did you do", "what have you done", "what did you change",
               "show the last changes", "show recent changes", "what changed",
               "what has changed"),
        "ru": ("что ты сделал", "что ты наделал", "покажи последние изменения",
               "какие были изменения", "что изменилось"),
        "es": ("qué hiciste", "que hiciste", "qué has hecho", "que has hecho",
               "qué cambiaste", "muestra los últimos cambios", "qué cambió",
               "que cambio"),
        "zh": ("你做了什么", "你都改了什么", "显示最近的改动", "有什么变化", "改了什么"),
    },
    "remember": {
        "en": ("remember that", "remember", "keep in mind that", "keep in mind",
               "don't forget that", "don't forget", "note that", "for the future"),
        "ru": ("запомни что", "запомни", "имей в виду что", "имей в виду",
               "на будущее", "не забудь что", "не забудь"),
        "es": ("recuerda que", "recuerda", "ten en cuenta que", "ten en cuenta",
               "no olvides que", "no olvides", "para el futuro"),
        "zh": ("记住", "记一下", "别忘了", "记得"),
    },
    "recall": {
        "en": ("what do you remember about me", "what do you remember",
               "show your memory", "what's in your memory", "what is in your memory"),
        "ru": ("что ты обо мне помнишь", "что ты помнишь", "покажи память",
               "что у тебя в памяти"),
        "es": ("qué recuerdas de mí", "qué recuerdas", "que recuerdas",
               "muestra tu memoria", "qué tienes en la memoria"),
        "zh": ("你记得我什么", "你记得什么", "显示记忆", "你的记忆里有什么"),
    },
    "forget": {
        "en": ("forget about", "forget that", "forget"),
        "ru": ("забудь про", "забудь что", "забудь"),
        "es": ("olvida lo de", "olvídate de", "olvidate de", "olvida que", "olvida"),
        "zh": ("忘掉", "忘记"),
    },
    "background": {
        "en": ("in the background", "in background", "do it later", "later on",
               "queue it up", "add a task", "on the side", "when you have time"),
        "ru": ("в фоне", "фоном", "займись", "потом сделай", "сделай потом",
               "поставь в очередь", "добавь задачу", "на потом"),
        "es": ("en segundo plano", "de fondo", "hazlo luego", "hazlo después",
               "ponlo en la cola", "añade una tarea", "cuando puedas"),
        "zh": ("后台", "在后台", "等会做", "排到队列", "加个任务"),
    },
    "status": {
        "en": ("what are you doing", "what are you working on", "what's in progress",
               "what is in progress", "what's running", "task status",
               "what's in the queue"),
        "ru": ("чем занят", "чем занимаешься", "что в работе", "что делаешь сейчас",
               "какие задачи", "что в очереди", "статус задач"),
        "es": ("qué estás haciendo", "que estas haciendo", "en qué trabajas",
               "qué hay en curso", "estado de las tareas", "qué hay en la cola"),
        "zh": ("你在做什么", "在忙什么", "任务状态", "有什么在跑", "队列里有什么"),
    },
    "ready": {
        "en": ("what's done", "what is done", "what's finished", "show what's finished",
               "what did you finish", "which tasks are done"),
        "ru": ("что готово", "покажи готовое", "что доделал", "какие задачи готовы"),
        "es": ("qué está listo", "que esta listo", "qué terminaste",
               "muestra lo terminado", "qué tareas están listas"),
        "zh": ("有什么做完了", "完成了什么", "显示完成的", "哪些任务好了"),
    },
    "cancel": {
        "en": ("cancel the task", "drop the task", "remove the task",
               "don't do the task", "forget the task"),
        "ru": ("отмени задачу", "брось задачу", "убери задачу", "не делай задачу"),
        "es": ("cancela la tarea", "quita la tarea", "no hagas la tarea",
               "olvida la tarea"),
        "zh": ("取消任务", "别做那个任务", "删掉任务"),
    },
    "continuation": {
        "en": ("go on", "keep going", "carry on", "continue", "finish it",
               "go ahead", "next"),
        "ru": ("продолжай", "добей", "давай", "дальше", "ок делай", "окей делай"),
        "es": ("sigue", "continúa", "continua", "dale", "termínalo", "adelante"),
        "zh": ("继续", "接着做", "做完它"),
    },
    "chat": {
        "en": ("what is", "what's a", "who is", "explain", "work out", "calculate",
               "translate", "what do you think", "how much is", "remind me",
               "what time", "is it worth", "what's the difference", "write an email"),
        "ru": ("что такое", "кто такой", "объясни", "посчитай", "сформулируй",
               "напиши письмо", "как думаешь", "переведи", "что он сказал",
               "что она сказала", "напомни", "во сколько", "сколько будет",
               "какая разница", "стоит ли"),
        "es": ("qué es", "que es", "quién es", "quien es", "explica", "calcula",
               "traduce", "qué opinas", "cuánto es", "recuérdame", "a qué hora",
               "vale la pena", "qué diferencia", "escribe un correo"),
        "zh": ("什么是", "谁是", "解释一下", "算一下", "翻译", "你怎么看",
               "多少钱", "提醒我", "几点", "值不值得", "有什么区别"),
    },
    # Filler that carries no routing weight in the learning model.
    "stopwords": {
        "en": ("the", "a", "an", "and", "in", "on", "to", "of", "it", "that",
               "this", "for", "my", "me", "is", "so", "just", "please"),
        "ru": ("и", "в", "на", "а", "что", "как", "мне", "мой", "это", "там",
               "по", "ну", "же"),
        "es": ("el", "la", "los", "las", "un", "una", "y", "en", "de", "que",
               "me", "mi", "esto", "eso", "por", "para", "pues"),
        "zh": ("的", "了", "在", "是", "我", "把", "就", "这", "那", "吧", "吗"),
    },
}

# --------------------------------------------------------------------------
# Routing signals. Technical tokens are the same in every language — people
# say "git", "npm" and "pytest" whatever they are speaking — so they live in
# one shared tuple instead of four copies.
# --------------------------------------------------------------------------
SHARED_CODE_STEMS = (
    "git", "npm", "pytest", "docker", "deployment", "kubernetes", "webpack",
    "gradle", "eslint", "tsc",
)
CODE_STEMS: dict[str, tuple[str, ...]] = {
    "en": ("test", "build", "deploy", "lint", "refactor", "compil", "migrat",
           "repositor", "function", "bug", "crash", "stack trace", "traceback",
           "file", "folder", "directory", "project", "module", "config",
           "dependenc", "script", "server", "service", "version", "branch",
           "commit", "merge", "rebase", "pull request", "revert", "roll back",
           "run the", "fix the", "check out"),
    "ru": ("коммит", "закоммить", "коммить", "ветк", "мердж", "пул реквест",
           "тест", "билд", "сборк", "деплой", "линт",
           "рефактор", "запусти", "исправ", "почини", "откати", "репозитор",
           "функци", "баг", "ошибк", "стек", "компилир", "миграц",
           "файл", "папк", "каталог", "проект", "модул", "конфиг",
           "зависимост", "скрипт", "сервер", "служб", "верси"),
    "es": ("prueba", "compil", "despliega", "despliegue", "refactor", "migraci",
           "repositorio", "función", "funcion", "error", "fallo", "traza",
           "archivo", "carpeta", "directorio", "proyecto", "módulo", "modulo",
           "config", "dependenci", "script", "servidor", "servicio", "versión",
           "version", "rama", "commit", "fusiona", "arregla", "repara",
           "ejecuta", "revierte"),
    "zh": ("测试", "构建", "部署", "重构", "编译", "迁移", "仓库", "函数",
           "报错", "错误", "堆栈", "文件", "目录", "文件夹", "项目", "模块",
           "配置", "依赖", "脚本", "服务器", "版本", "分支", "代码", "端口",
           "日志", "提交", "合并", "修复", "运行一下", "跑一下"),
}
#: Short words that drag in unrelated ones as stems ("log" in "logic",
#: "порт" in "спорт"), so they are matched whole, case by case.
CODE_WORDS: dict[str, tuple[str, ...]] = {
    "en": ("pr", "ci", "log", "logs", "code", "class", "classes", "port",
           "ports", "repo", "diff", "api", "db"),
    "ru": ("pr", "лог", "лога", "логи", "логов", "логах", "логами",
           "код", "кода", "коде", "кодом", "коды", "кодов",
           "класс", "класса", "классе", "классы", "классов",
           "порт", "порта", "порту", "порты", "портов"),
    "es": ("pr", "ci", "log", "logs", "código", "codigo", "clase", "clases",
           "puerto", "puertos", "repo", "api"),
    "zh": ("pr", "ci", "api"),
}


def group(name: str, lang: str) -> tuple[str, ...]:
    """One language's phrases for a command."""
    return PHRASES[name].get(lang, ())


def every(name: str) -> tuple[str, ...]:
    """Every language's phrases for a command, longest first.

    Longest first matters wherever a phrase is stripped off the front of an
    utterance: «запомни что я работаю по ночам» must lose «запомни что», not
    just «запомни», or the fact starts with a dangling conjunction.
    """
    seen: list[str] = []
    for lang in LANGUAGES:
        for phrase in PHRASES[name].get(lang, ()):
            if phrase not in seen:
                seen.append(phrase)
    return tuple(sorted(seen, key=len, reverse=True))


def code_patterns() -> tuple[tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]]:
    """Compiled (stem, whole-word) routing signals across all languages."""
    stems: list[str] = list(SHARED_CODE_STEMS)
    words: list[str] = []
    for lang in LANGUAGES:
        stems.extend(CODE_STEMS.get(lang, ()))
        words.extend(CODE_WORDS.get(lang, ()))
    stem_res = tuple(re.compile(_stem_pattern(s), re.I) for s in dict.fromkeys(stems))
    word_res = tuple(re.compile(_word_pattern(w), re.I) for w in dict.fromkeys(words))
    return stem_res, word_res


def _stem_pattern(stem: str) -> str:
    # Chinese writes no spaces, so a word boundary before the stem would never
    # match; every other language needs one, or «порт» fires inside «спорт».
    return re.escape(stem) if _cjk(stem) else r"(?<!\w)" + re.escape(stem)


def _word_pattern(word: str) -> str:
    return re.escape(word) if _cjk(word) else r"\b" + re.escape(word) + r"\b"


def strip_openers(text: str) -> str:
    """Drop the address and the throat-clearing in front of a command."""
    openers = every("openers")
    if _cjk(text):
        lowered = text.strip()
        changed = True
        while changed:
            changed = False
            for opener in openers:
                if lowered.startswith(opener) and len(lowered) > len(opener):
                    lowered = lowered[len(opener):].lstrip(" ,.:，。、—-")
                    changed = True
                    break
        return lowered
    words = normalise(text).split()
    while words and words[0] in openers:
        words.pop(0)
    return " ".join(words)


def starts_with(text: str, phrases: tuple[str, ...]) -> bool:
    """Is this a command to the shell rather than talk about one?

    The phrase has to open the utterance — after the address, but not after a
    clause. «А это можно откатить?» is a question, not an undo.
    """
    lowered = strip_openers(text)
    if not lowered:
        return False
    for phrase in phrases:
        if _cjk(phrase):
            if lowered.startswith(phrase):
                return True
            continue
        if lowered == phrase or lowered.startswith(phrase + " "):
            return True
    return False


def contains(text: str, phrases: tuple[str, ...]) -> bool:
    """Anywhere in the utterance — for phrases that trail, like «in background»."""
    lowered = text.lower()
    return any(phrase in lowered for phrase in phrases)


def tail_after(text: str, phrases: tuple[str, ...]) -> str | None:
    """What is left once the command phrase is taken off the front.

    Returns None when no phrase opened the utterance, so callers can tell
    «remember that I work at night» from «what do you remember».
    """
    stripped = text.strip()
    lowered = stripped.lower()
    for phrase in phrases:
        if lowered.startswith(phrase):
            return stripped[len(phrase):].strip(" ,.:—-，。、").strip()
    return None
