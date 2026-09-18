"""Everything the shell says, in every language it speaks.

The shell has two language-shaped surfaces. This module is the first one:
the words that come *out* — spoken replies, prompt scaffolding, reasons for
failure. The second one, the phrases it listens *for*, lives in `lexicon`.

Output language is decided per utterance, not per install, because a voice
assistant that answers in the wrong language is useless even when it is
right. The order is: what the client declared in `hello`, then what the
person actually just spoke, then `VOICE_LANG`, then English.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Iterator

LANGUAGES = ("en", "ru", "es", "zh")
FALLBACK = "en"

#: Sentence/list separators differ enough to be worth a table.
SEPARATORS = {"en": ", ", "ru": ", ", "es": ", ", "zh": "、"}

_CYRILLIC = re.compile(r"[а-яё]", re.I)
_CJK = re.compile(r"[一-鿿]")
#: Short, common, and unlikely to appear in the other three languages.
_SPANISH_HINTS = re.compile(
    r"(?<!\w)(qué|que|cómo|como|por favor|dime|hazlo|archivo|código|prueba|"
    r"arregla|el|la|los|las|una|está|hola|gracias|ahora|dónde)(?!\w)", re.I)
_ENGLISH_HINTS = re.compile(
    r"(?<!\w)(the|and|please|fix|run|what|how|show|file|code|test|undo|"
    r"remember|commit|branch|deploy)(?!\w)", re.I)

_current = FALLBACK


def normalize(tag: str | None) -> str | None:
    """`ru-RU` → `ru`, `zh-Hans-CN` → `zh`, junk → None."""
    if not tag:
        return None
    base = str(tag).strip().replace("_", "-").split("-")[0].lower()
    return base if base in LANGUAGES else None


def detect(text: str) -> str | None:
    """Which of the four languages this utterance is in, or None if unclear.

    Script decides Russian and Chinese outright. Latin script needs a guess,
    and a wrong guess is cheap to fix (the next utterance re-decides) while a
    refusal to guess is not: the person hears English at a Spanish phone.
    """
    if not text:
        return None
    if _CJK.search(text):
        return "zh"
    if _CYRILLIC.search(text):
        return "ru"
    if not re.search(r"[a-z]", text, re.I):
        return None
    spanish = len(_SPANISH_HINTS.findall(text))
    english = len(_ENGLISH_HINTS.findall(text))
    if spanish > english:
        return "es"
    if english > spanish:
        return "en"
    return None


def default_language() -> str:
    """`VOICE_LANG` for the deployment; English when nothing says otherwise."""
    return normalize(os.environ.get("VOICE_LANG")) or FALLBACK


def use(lang: str | None) -> str:
    """Speak this language from now on. Unknown tags leave the choice alone."""
    global _current
    chosen = normalize(lang)
    if chosen:
        _current = chosen
    return _current


def current() -> str:
    return _current


def reset() -> None:
    """Back to the deployment default — used at startup and between tests."""
    global _current
    _current = default_language()


@contextmanager
def speaking(lang: str | None) -> Iterator[str]:
    """Answer one utterance in another language without changing the default."""
    global _current
    previous = _current
    use(lang)
    try:
        yield _current
    finally:
        _current = previous


def t(key: str, **kw: object) -> str:
    """One phrase in the current language.

    A missing translation falls back to English rather than to the key: the
    person hears a real sentence in the wrong language instead of `undo.done`.
    """
    entry = PHRASES[key]
    text = entry.get(_current) or entry[FALLBACK]
    return text.format(**kw) if kw else text


def join(items: object, lang: str | None = None) -> str:
    """List separator that does not read as a typo in Chinese."""
    return SEPARATORS.get(lang or _current, ", ").join(items)  # type: ignore[arg-type]


def uses_stress_marks(lang: str | None = None) -> bool:
    """Russian synthesis needs `+` before the stressed vowel. Nothing else does."""
    return (lang or _current) == "ru"


# --------------------------------------------------------------------------
# The catalogue. Russian is the original wording, kept verbatim: it is what
# the daily user hears, and a "cleaner" rewrite is a regression they did not
# ask for. English is the reference translation; Spanish and Chinese follow it.
# --------------------------------------------------------------------------
PHRASES: dict[str, dict[str, str]] = {
    # -- the shell itself ---------------------------------------------------
    "shell.failure": {
        "en": "Something broke in the shell. {reason}",
        "ru": "Сбой в оболочке. {reason}",
        "es": "Algo falló en la interfaz. {reason}",
        "zh": "外壳出错了。{reason}",
    },
    "shell.stopped": {
        "en": "Stopped.", "ru": "Остановил.", "es": "Detenido.", "zh": "已停止。",
    },
    "shell.working": {
        "en": "Working on it.", "ru": "Работаю.",
        "es": "Trabajando.", "zh": "正在处理。",
    },
    "shell.still_working": {
        "en": "Still working.", "ru": "Ещё работаю.",
        "es": "Sigo trabajando.", "zh": "还在处理。",
    },
    "shell.nothing_to_clarify": {
        "en": "Nothing to clarify.", "ru": "Нечего уточнять.",
        "es": "No hay nada que aclarar.", "zh": "没有需要确认的事。",
    },
    "shell.allowed_once": {
        "en": "Allowed once. I won't remember this one.",
        "ru": "Разрешил один раз. Это я запоминать не буду.",
        "es": "Permitido una vez. Esto no lo voy a recordar.",
        "zh": "只允许这一次，我不会记住它。",
    },
    # -- handing the conversation between targets ---------------------------
    "handoff.nothing": {
        "en": "Nothing to hand over yet.", "ru": "Пока нечего перекидывать.",
        "es": "Todavía no hay nada que pasar.", "zh": "暂时没有可以转交的内容。",
    },
    "handoff.header": {
        "en": "[handed over from the «{source}» target]",
        "ru": "[передача из цели «{source}»]",
        "es": "[traspaso desde el destino «{source}»]",
        "zh": "[来自「{source}」目标的转交]",
    },
    "handoff.asked": {
        "en": "the question was: {text}", "ru": "спрашивали: {text}",
        "es": "la pregunta era: {text}", "zh": "问的是：{text}",
    },
    "handoff.answer": {
        "en": "the answer was: {text}", "ru": "ответ был: {text}",
        "es": "la respuesta fue: {text}", "zh": "回答是：{text}",
    },
    # -- checkpoints and undo ----------------------------------------------
    "undo.no_checkpoint": {
        "en": "I couldn't record a checkpoint: «undo that» won't work here.",
        "ru": "Точку отката записать не вышло: «откати последнее» здесь не сработает.",
        "es": "No pude guardar un punto de retorno: aquí «deshaz eso» no va a funcionar.",
        "zh": "没能记下回滚点，这里说「撤销刚才的」不会起作用。",
    },
    "undo.nothing": {
        "en": "There's nothing to undo.", "ru": "Откатывать нечего.",
        "es": "No hay nada que deshacer.", "zh": "没有可以撤销的内容。",
    },
    "undo.busy": {
        "en": "Work is still running, undoing now is risky. Say «stop working».",
        "ru": "Работа ещё идёт, откатывать сейчас опасно. Скажи «останови работу».",
        "es": "Todavía estoy trabajando, deshacer ahora es arriesgado. Di «detén el trabajo».",
        "zh": "任务还在进行，现在撤销有风险。可以说「停止工作」。",
    },
    "undo.failed": {
        "en": "The undo didn't go through: {reason}",
        "ru": "Откатить не вышло: {reason}",
        "es": "No se pudo deshacer: {reason}",
        "zh": "撤销没有成功：{reason}",
    },
    "undo.done": {
        "en": "Rolled back {changed}. You had said: {title}",
        "ru": "Откатил {changed}. Сказано было: {title}",
        "es": "Deshecho {changed}. Habías dicho: {title}",
        "zh": "已回滚{changed}。你当时说的是：{title}",
    },
    "history.empty": {
        "en": "I haven't changed anything yet.", "ru": "Я пока ничего не менял.",
        "es": "Todavía no he cambiado nada.", "zh": "我还没有改过任何东西。",
    },
    "history.last": {
        "en": "Most recent: {items}.", "ru": "Последнее: {items}.",
        "es": "Lo último: {items}.", "zh": "最近的是：{items}。",
    },
    "checkpoint.commit": {
        "en": "by voice: {said}", "ru": "голосом: {said}",
        "es": "por voz: {said}", "zh": "语音操作：{said}",
    },
    "changes.none": {
        "en": "no file changes", "ru": "без изменений в файлах",
        "es": "sin cambios en archivos", "zh": "文件没有变化",
    },
    "changes.two": {
        "en": "{first} and {second}", "ru": "{first} и {second}",
        "es": "{first} y {second}", "zh": "{first}和{second}",
    },
    "changes.more": {
        "en": "{first}, {second} and {rest} more",
        "ru": "{first}, {second} и ещё {rest}",
        "es": "{first}, {second} y {rest} más",
        "zh": "{first}、{second}，还有另外{rest}个",
    },
    # -- targets ------------------------------------------------------------
    "target.failed": {
        "en": "{target}: I couldn't do it. {reason}",
        "ru": "{target}: не смог выполнить. {reason}",
        "es": "{target}: no pude hacerlo. {reason}",
        "zh": "{target}：没能完成。{reason}",
    },
    "target.unavailable": {
        "en": "Claude is unavailable: {reason}.",
        "ru": "Claude недоступен: {reason}.",
        "es": "Claude no está disponible: {reason}.",
        "zh": "Claude 不可用：{reason}。",
    },
    "target.denied_by_voice": {
        "en": "Denied by voice", "ru": "Отклонено голосом",
        "es": "Rechazado por voz", "zh": "已通过语音拒绝",
    },
    "target.chat_cannot": {
        "en": "{tool} is not available in the chat target — say «switch to code» "
              "if an action is needed",
        "ru": "{tool} недоступен в разговорной цели — скажи «в код», если нужно действие",
        "es": "{tool} no está disponible en el destino de conversación — di «pasa a código» "
              "si hace falta una acción",
        "zh": "{tool} 在对话目标里不可用——需要动手改东西的话，说「切到代码」",
    },
    "note.saved": {
        "en": "Noted.", "ru": "Записал.", "es": "Anotado.", "zh": "记下了。",
    },
    "stub.no_sdk": {
        "en": "claude-agent-sdk is not installed",
        "ru": "не установлен claude-agent-sdk",
        "es": "claude-agent-sdk no está instalado",
        "zh": "没有安装 claude-agent-sdk",
    },
    "stub.bad_token": {
        "en": "the access token is not valid — {problem}",
        "ru": "токен доступа неверный — {problem}",
        "es": "el token de acceso no es válido — {problem}",
        "zh": "访问令牌无效——{problem}",
    },
    "stub.no_subscription": {
        "en": "no Claude subscription is connected",
        "ru": "не подключена подписка Claude",
        "es": "no hay ninguna suscripción de Claude conectada",
        "zh": "没有连接 Claude 订阅",
    },
    # -- memory -------------------------------------------------------------
    "memory.saved": {
        "en": "Got it.", "ru": "Запомнил.", "es": "Lo recordaré.", "zh": "记住了。",
    },
    "memory.nothing_to_save": {
        "en": "There's nothing to remember there.", "ru": "Нечего запоминать.",
        "es": "Ahí no hay nada que recordar.", "zh": "这里没有需要记住的内容。",
    },
    "memory.forgot": {
        "en": "Forgotten.", "ru": "Забыл.", "es": "Olvidado.", "zh": "忘掉了。",
    },
    "memory.not_remembered": {
        "en": "I don't have that.", "ru": "Такого не помню.",
        "es": "No tengo eso.", "zh": "我不记得这个。",
    },
    "memory.recall": {
        "en": "I remember: {facts}.", "ru": "Помню: {facts}.",
        "es": "Recuerdo: {facts}.", "zh": "我记得：{facts}。",
    },
    "memory.empty": {
        "en": "I don't remember anything yet.", "ru": "Пока ничего не помню.",
        "es": "Todavía no recuerdo nada.", "zh": "我还什么都不记得。",
    },
    "memory.header": {
        "en": "# Voice Shell memory\n\nWhat Claude knows about its owner and their projects.\n",
        "ru": "# Память Voice Shell\n\nЧто Claude знает о хозяине и его проектах.\n",
        "es": "# Memoria de Voice Shell\n\nLo que Claude sabe sobre su dueño y sus proyectos.\n",
        "zh": "# Voice Shell 记忆\n\nClaude 知道的关于主人和他的项目的事。\n",
    },
    "memory.hint_prefix": {
        "en": "[memory] ", "ru": "[память] ", "es": "[memoria] ", "zh": "[记忆] ",
    },
    "reroute.nothing": {
        "en": "There's nothing to reroute.", "ru": "Нечего перенаправлять.",
        "es": "No hay nada que redirigir.", "zh": "没有可以重新转发的内容。",
    },
    # -- speech recognition hints ------------------------------------------
    "asr.alternatives": {
        "en": "[recognition] other readings of the same words: ",
        "ru": "[распознавание] другие варианты того же: ",
        "es": "[reconocimiento] otras lecturas de lo mismo: ",
        "zh": "[识别] 同一句话的其他可能：",
    },
    "ambient.transcript_header": {
        "en": "[ambient] recent utterances:", "ru": "[ambient] последние реплики:",
        "es": "[ambient] últimas frases:", "zh": "[环境] 最近的对话：",
    },
    "ambient.me": {"en": "me", "ru": "я", "es": "yo", "zh": "我"},
    "speaker.master": {
        "en": "the owner is speaking", "ru": "говорит мастер",
        "es": "habla el dueño", "zh": "主人在说话",
    },
    "speaker.bystander": {
        "en": "someone else is speaking", "ru": "говорит собеседник",
        "es": "habla otra persona", "zh": "旁人在说话",
    },
    "speaker.unknown": {
        "en": "unrecognised speaker", "ru": "неопределённый говорящий",
        "es": "hablante no reconocido", "zh": "无法确定说话人",
    },
    "speaker.self_echo": {
        "en": "an echo of our own speech", "ru": "эхо собственного TTS",
        "es": "eco de nuestra propia voz", "zh": "自己合成语音的回声",
    },
    "ambient.bystander": {
        "en": "someone else", "ru": "собеседник",
        "es": "otra persona", "zh": "旁人",
    },
    # -- второе ухо ---------------------------------------------------------
    "ambient.on": {
        "en": "Second ear is open. I'm listening to what's around and will retell it "
              "when you ask. Recording other people needs their consent.",
        "ru": "Второе ухо открыто. Слушаю, что вокруг, и перескажу, когда спросишь. "
              "Запись чужой речи требует согласия собеседников.",
        "es": "Segundo oído abierto. Escucho lo que hay alrededor y te lo cuento cuando "
              "preguntes. Grabar a otras personas necesita su consentimiento.",
        "zh": "第二只耳朵开了。我听着周围，你问我就转述。录别人说的话需要对方同意。",
    },
    "ambient.off": {
        "en": "Second ear is closed, and what it heard is wiped.",
        "ru": "Второе ухо закрыто, услышанное стёрто.",
        "es": "Segundo oído cerrado, y lo que oyó está borrado.",
        "zh": "第二只耳朵关了，听到的内容也清掉了。",
    },
    "ambient.already_on": {
        "en": "Second ear is already open.", "ru": "Второе ухо и так открыто.",
        "es": "El segundo oído ya está abierto.", "zh": "第二只耳朵已经开着了。",
    },
    "ambient.already_off": {
        "en": "Second ear wasn't open.", "ru": "Второе ухо и не было открыто.",
        "es": "El segundo oído no estaba abierto.", "zh": "第二只耳朵本来就没开。",
    },
    "ambient.nothing_heard": {
        "en": "I haven't heard anything around yet.",
        "ru": "Вокруг я пока ничего не слышал.",
        "es": "Todavía no he oído nada alrededor.",
        "zh": "我还没听到周围有什么。",
    },
    "ambient.closed": {
        "en": "Second ear is closed — say «second ear» and I'll listen.",
        "ru": "Второе ухо закрыто — скажи «второе ухо», и я буду слушать.",
        "es": "El segundo oído está cerrado — di «segundo oído» y escucho.",
        "zh": "第二只耳朵关着——说「第二只耳朵」，我就开始听。",
    },
    "glossary.hint": {
        "en": "[glossary] names that occur in this project: {terms}. If something "
              "in the utterance sounds like one of these names, assume it was meant.",
        "ru": "[глоссарий] в этом проекте встречаются: {terms}. Если в реплике "
              "что-то звучит похоже на одно из этих имён, считай, что имелось в виду оно.",
        "es": "[glosario] nombres que aparecen en este proyecto: {terms}. Si algo en "
              "la frase suena parecido a uno de ellos, da por hecho que era ese.",
        "zh": "[术语表] 这个项目里出现的名称：{terms}。如果话里有什么听起来像其中之一，"
              "就按那个理解。",
    },
    # -- telegram bridge ----------------------------------------------------
    "telegram.not_configured": {
        "en": "Telegram isn't set up. On the server: {command}",
        "ru": "Телеграм не настроен. На сервере: {command}",
        "es": "Telegram no está configurado. En el servidor: {command}",
        "zh": "Telegram 还没有配置。在服务器上执行：{command}",
    },
    "telegram.unclear": {
        "en": "I didn't catch what to send.", "ru": "Не понял, что отправить.",
        "es": "No entendí qué hay que enviar.", "zh": "我没听清要发送什么。",
    },
    "telegram.sent_one": {
        "en": "Sent to Telegram.", "ru": "Отправил в телеграм.",
        "es": "Enviado a Telegram.", "zh": "已发送到 Telegram。",
    },
    "telegram.not_sent": {
        "en": "Not sent: {problem}", "ru": "Не отправил: {problem}",
        "es": "No se envió: {problem}", "zh": "没有发送：{problem}",
    },
    "telegram.sent_list": {
        "en": "Sent: {names}", "ru": "Отправил: {names}",
        "es": "Enviado: {names}", "zh": "已发送：{names}",
    },
    "telegram.and_more": {
        "en": " and {rest} more", "ru": " и ещё {rest}",
        "es": " y {rest} más", "zh": "，还有另外{rest}个",
    },
    "telegram.refused_list": {
        "en": "Not sent — {reasons}", "ru": "Не отправил — {reasons}",
        "es": "No se envió — {reasons}", "zh": "没有发送——{reasons}",
    },
    "telegram.nothing": {
        "en": "There's nothing to send.", "ru": "Нечего отправлять.",
        "es": "No hay nada que enviar.", "zh": "没有可以发送的内容。",
    },
    "telegram.unreadable": {
        "en": "the file can't be read", "ru": "файл не читается",
        "es": "el archivo no se puede leer", "zh": "文件读不出来",
    },
    "telegram.missing": {
        "en": "there's no such file", "ru": "такого файла нет",
        "es": "no existe ese archivo", "zh": "没有这个文件",
    },
    "telegram.outside": {
        "en": "the file is outside the working directory",
        "ru": "файл вне рабочего каталога",
        "es": "el archivo está fuera del directorio de trabajo",
        "zh": "文件不在工作目录里",
    },
    "telegram.secret": {
        "en": "looks like a secret — I don't send those out",
        "ru": "похоже на секрет — такое наружу не отправляю",
        "es": "parece un secreto — eso no lo mando fuera",
        "zh": "看起来像密钥，这种东西我不往外发",
    },
    "telegram.too_big": {
        "en": "too large: {size} MB", "ru": "слишком большой: {size} МБ",
        "es": "demasiado grande: {size} MB", "zh": "太大了：{size} MB",
    },
    "telegram.empty": {
        "en": "the file is empty", "ru": "файл пустой",
        "es": "el archivo está vacío", "zh": "文件是空的",
    },
    "telegram.not_set_up": {
        "en": "Telegram isn't set up", "ru": "телеграм не настроен",
        "es": "Telegram no está configurado", "zh": "Telegram 还没有配置",
    },
    "telegram.bad_token_shape": {
        "en": "the bot token doesn't look real — it should look like 1234567890:ABC…",
        "ru": "токен бота не похож на настоящий — он вида 1234567890:ABC…",
        "es": "el token del bot no parece real — tiene la forma 1234567890:ABC…",
        "zh": "机器人令牌看起来不对，它应该长成 1234567890:ABC… 这样",
    },
    "telegram.bad_chat": {
        "en": "the chat id has to be a number", "ru": "номер чата должен быть числом",
        "es": "el id del chat tiene que ser un número", "zh": "聊天 ID 必须是数字",
    },
    "telegram.token_rejected": {
        "en": "Telegram rejected the bot token", "ru": "телеграм не принял токен бота",
        "es": "Telegram rechazó el token del bot", "zh": "Telegram 不接受这个机器人令牌",
    },
    "telegram.unknown_chat": {
        "en": "Telegram doesn't know that chat — message the bot first",
        "ru": "телеграм не знает такого чата — напиши боту первым",
        "es": "Telegram no conoce ese chat — escríbele al bot primero",
        "zh": "Telegram 不认识这个聊天——先给机器人发条消息",
    },
    "telegram.refused": {
        "en": "Telegram refused: {detail}", "ru": "телеграм отказал: {detail}",
        "es": "Telegram lo rechazó: {detail}", "zh": "Telegram 拒绝了：{detail}",
    },
    "telegram.unreachable": {
        "en": "Telegram is unreachable", "ru": "до телеграма не достучаться",
        "es": "no se puede alcanzar Telegram", "zh": "连不上 Telegram",
    },
    "telegram.garbled": {
        "en": "Telegram answered with something I can't read",
        "ru": "телеграм ответил непонятным",
        "es": "Telegram respondió con algo que no entiendo",
        "zh": "Telegram 的回复看不懂",
    },
    # -- background tasks ---------------------------------------------------
    "tasks.repo_only": {
        "en": "Background tasks only work inside a repository.",
        "ru": "Фоновые задачи работают только в репозитории.",
        "es": "Las tareas en segundo plano solo funcionan dentro de un repositorio.",
        "zh": "后台任务只能在代码仓库里运行。",
    },
    "tasks.accepted": {
        "en": "Started on it: {title}. I'll tell you when it's done.",
        "ru": "Взял в работу: {title}. Скажу, когда будет.",
        "es": "Me pongo con ello: {title}. Te aviso cuando esté.",
        "zh": "开始处理：{title}。做完了我会说。",
    },
    "tasks.none_ready": {
        "en": "Nothing is finished yet.", "ru": "Готовых задач нет.",
        "es": "Todavía no hay nada terminado.", "zh": "还没有完成的任务。",
    },
    "tasks.ready_report": {
        "en": "{count} finished. The last one: {title}. {summary}",
        "ru": "Готово {count}. Последняя: {title}. {summary}",
        "es": "{count} terminadas. La última: {title}. {summary}",
        "zh": "完成了{count}个。最后一个：{title}。{summary}",
    },
    "tasks.not_found": {
        "en": "I couldn't find that task.", "ru": "Не нашёл такой задачи.",
        "es": "No encontré esa tarea.", "zh": "没有找到这个任务。",
    },
    "tasks.cancelled": {
        "en": "Cancelled: {title}.", "ru": "Отменил: {title}.",
        "es": "Cancelada: {title}.", "zh": "已取消：{title}。",
    },
    "tasks.stalled_prefix": {
        "en": "A task got stuck. ", "ru": "Встала задача. ",
        "es": "Una tarea se atascó. ", "zh": "有个任务卡住了。",
    },
    "tasks.idle": {
        "en": "I'm not doing anything.", "ru": "Ничего не делаю.",
        "es": "No estoy haciendo nada.", "zh": "我什么都没在做。",
    },
    "tasks.all_done": {
        "en": "All done, {count} finished and waiting.",
        "ru": "Всё сделано, готовых задач {count}.",
        "es": "Todo hecho, {count} terminadas esperando.",
        "zh": "都做完了，有{count}个完成的任务在等着。",
    },
    "tasks.doing": {
        "en": "Working on: {title}, {minutes} minutes in",
        "ru": "Делаю: {title}, уже {minutes} минут",
        "es": "Trabajando en: {title}, ya {minutes} minutos",
        "zh": "正在做：{title}，已经{minutes}分钟",
    },
    "tasks.and_more": {
        "en": "and {rest} more", "ru": "и ещё {rest}",
        "es": "y {rest} más", "zh": "还有另外{rest}个",
    },
    "tasks.queued": {
        "en": "{count} in the queue", "ru": "в очереди {count}",
        "es": "{count} en la cola", "zh": "队列里有{count}个",
    },
    "tasks.state.queued": {
        "en": "waiting", "ru": "ждёт", "es": "esperando", "zh": "等待中",
    },
    "tasks.state.running": {
        "en": "running", "ru": "в работе", "es": "en curso", "zh": "进行中",
    },
    "tasks.state.done": {
        "en": "done", "ru": "готова", "es": "lista", "zh": "已完成",
    },
    "tasks.state.stuck": {
        "en": "stuck", "ru": "встала", "es": "atascada", "zh": "已卡住",
    },
    "tasks.state.cancelled": {
        "en": "cancelled", "ru": "отменена", "es": "cancelada", "zh": "已取消",
    },
    "tasks.interrupted_by_restart": {
        "en": "interrupted when the service restarted",
        "ru": "прервалась при перезапуске службы",
        "es": "se interrumpió al reiniciarse el servicio",
        "zh": "服务重启时被打断了",
    },
    "tasks.did_not_start": {
        "en": "didn't start: {reason}", "ru": "не завелась: {reason}",
        "es": "no arrancó: {reason}", "zh": "没能启动：{reason}",
    },
    "tasks.slug_fallback": {
        "en": "task", "ru": "задача", "es": "tarea", "zh": "task",
    },
    "tasks.commit": {
        "en": "in background: {said}", "ru": "фоном: {said}",
        "es": "en segundo plano: {said}", "zh": "后台任务：{said}",
    },
    # -- summaries read aloud ----------------------------------------------
    "fmt.fixed_one": {
        "en": "Fixed {first}.", "ru": "Исправил {first}.",
        "es": "Arreglado {first}.", "zh": "改好了{first}。",
    },
    "fmt.fixed_list": {
        "en": "Fixed {names}.", "ru": "Исправил {names}.",
        "es": "Arreglado {names}.", "zh": "改好了{names}。",
    },
    "fmt.fixed_more": {
        "en": "Fixed {first}, {second} and {rest} more files.",
        "ru": "Исправил {first}, {second} и ещё {rest} файла.",
        "es": "Arreglado {first}, {second} y {rest} archivos más.",
        "zh": "改好了{first}、{second}，还有另外{rest}个文件。",
    },
    "fmt.and": {"en": "and", "ru": "и", "es": "y", "zh": "和"},
    "fmt.tests_failed": {
        "en": "{count} tests are failing.", "ru": "{count} тестов падают.",
        "es": "{count} pruebas están fallando.", "zh": "有{count}个测试失败。",
    },
    "fmt.tests_passed": {
        "en": "All {count} tests pass.", "ru": "Все {count} тестов проходят.",
        "es": "Las {count} pruebas pasan.", "zh": "{count}个测试全部通过。",
    },
    "fmt.permission_known": {
        "en": "Claude wants to {phrase}. Allow?",
        "ru": "Клод хочет {phrase}. Разрешить?",
        "es": "Claude quiere {phrase}. ¿Lo permito?",
        "zh": "Claude 想{phrase}。允许吗？",
    },
    "fmt.permission_tool": {
        "en": "Claude wants to run: {tool}. Allow?",
        "ru": "Клод хочет выполнить: {tool}. Разрешить?",
        "es": "Claude quiere ejecutar: {tool}. ¿Lo permito?",
        "zh": "Claude 想执行：{tool}。允许吗？",
    },
    "fmt.tool.rm_build": {
        "en": "delete the old build folder", "ru": "удалить старую папку build",
        "es": "borrar la carpeta build vieja", "zh": "删掉旧的 build 目录",
    },
    "fmt.tool.npm_install": {
        "en": "install dependencies", "ru": "установить зависимости",
        "es": "instalar dependencias", "zh": "安装依赖",
    },
    "fmt.tool.git_push": {
        "en": "push the changes", "ru": "запушить изменения",
        "es": "subir los cambios", "zh": "推送改动",
    },
    "fmt.tool.git_commit": {
        "en": "commit the changes", "ru": "закоммитить изменения",
        "es": "confirmar los cambios", "zh": "提交改动",
    },
    "fmt.tool.tests": {
        "en": "run the tests", "ru": "запустить тесты",
        "es": "ejecutar las pruebas", "zh": "跑测试",
    },
    # -- why something failed ----------------------------------------------
    "reason.root": {
        "en": "the service runs as root, and the CLI refused the no-questions mode",
        "ru": "служба работает от root, и CLI не принял режим без вопросов",
        "es": "el servicio corre como root y el CLI rechazó el modo sin preguntas",
        "zh": "服务以 root 运行，CLI 不接受免询问模式",
    },
    "reason.quota": {
        "en": "the subscription limit ran out", "ru": "кончился лимит подписки",
        "es": "se acabó el límite de la suscripción", "zh": "订阅额度用完了",
    },
    "reason.overloaded": {
        "en": "Claude is overloaded right now", "ru": "Claude сейчас перегружен",
        "es": "Claude está sobrecargado ahora mismo", "zh": "Claude 现在过载了",
    },
    "reason.disk": {
        "en": "the disk is out of space", "ru": "на диске кончилось место",
        "es": "no queda espacio en disco", "zh": "磁盘没有空间了",
    },
    "reason.network": {
        "en": "there's no network", "ru": "нет сети",
        "es": "no hay red", "zh": "没有网络",
    },
    "reason.not_repo": {
        "en": "the working directory isn't a repository",
        "ru": "рабочий каталог — не репозиторий",
        "es": "el directorio de trabajo no es un repositorio",
        "zh": "工作目录不是代码仓库",
    },
    "reason.timeout": {
        "en": "the answer didn't arrive in time", "ru": "ответ не пришёл вовремя",
        "es": "la respuesta no llegó a tiempo", "zh": "回复没有及时到达",
    },
    "reason.permission": {
        "en": "not enough permissions", "ru": "не хватило прав",
        "es": "faltaron permisos", "zh": "权限不够",
    },
    "reason.not_found": {
        "en": "a needed file wasn't found", "ru": "не нашёлся нужный файл",
        "es": "no se encontró un archivo necesario", "zh": "没有找到需要的文件",
    },
    "reason.session": {
        "en": "the Claude session didn't come up", "ru": "сессия Claude не поднялась",
        "es": "la sesión de Claude no arrancó", "zh": "Claude 会话没有启动起来",
    },
    "reason.token_rejected": {
        "en": "the access token wasn't accepted", "ru": "токен доступа не принят",
        "es": "el token de acceso no fue aceptado", "zh": "访问令牌没有被接受",
    },
    "reason.api_error": {
        "en": "Claude answered with an error", "ru": "Claude ответил ошибкой",
        "es": "Claude respondió con un error", "zh": "Claude 返回了一个错误",
    },
    "reason.max_turns": {
        "en": "The work didn't fit in the steps it was given.",
        "ru": "Работа не уложилась в отведённые шаги.",
        "es": "El trabajo no cupo en los pasos disponibles.",
        "zh": "工作没能在允许的步数里做完。",
    },
    "reason.in_log": {
        "en": "The reason is in the service log.", "ru": "Причина в логе службы.",
        "es": "El motivo está en el log del servicio.", "zh": "原因在服务日志里。",
    },
    # -- the watcher --------------------------------------------------------
    "watch.build_default_name": {
        "en": "build", "ru": "сборка", "es": "compilación", "zh": "构建",
    },
    "watch.build_failed": {
        "en": "Build {name} failed.", "ru": "Упала сборка {name}.",
        "es": "Falló la compilación {name}.", "zh": "构建 {name} 失败了。",
    },
    "watch.build_failed_branch": {
        "en": "Build {name} failed on branch {branch}.",
        "ru": "Упала сборка {name} на ветке {branch}.",
        "es": "Falló la compilación {name} en la rama {branch}.",
        "zh": "分支 {branch} 上的构建 {name} 失败了。",
    },
    "watch.fix_prompt": {
        "en": "Build «{name}» on branch {branch} failed. Read the logs with gh, "
              "find the cause and fix it.",
        "ru": "Сборка «{name}» на ветке {branch} упала. Посмотри логи через gh, "
              "найди причину и почини.",
        "es": "La compilación «{name}» en la rama {branch} falló. Mira los logs con gh, "
              "encuentra la causa y arréglalo.",
        "zh": "分支 {branch} 上的构建「{name}」失败了。用 gh 看日志，找出原因并修好。",
    },
    # -- connecting an account ---------------------------------------------
    "auth.no_link": {
        "en": "couldn't get the authorisation link",
        "ru": "не удалось получить ссылку авторизации",
        "es": "no se pudo obtener el enlace de autorización",
        "zh": "没能拿到授权链接",
    },
    "auth.flow_not_started": {
        "en": "the flow hasn't been started", "ru": "флоу не запущен",
        "es": "el flujo no se ha iniciado", "zh": "流程还没有启动",
    },
    "auth.code_rejected": {
        "en": "the code wasn't accepted, or no token was issued",
        "ru": "код не принят или токен не выдан",
        "es": "el código no fue aceptado o no se emitió ningún token",
        "zh": "验证码没有被接受，或者没有发放令牌",
    },
    "auth.code_rejected_detail": {
        "en": "the code wasn't accepted, or no token was issued. The CLI said: {detail}",
        "ru": "код не принят или токен не выдан. CLI ответил: {detail}",
        "es": "el código no fue aceptado o no se emitió ningún token. El CLI dijo: {detail}",
        "zh": "验证码没有被接受，或者没有发放令牌。CLI 的回复是：{detail}",
    },
    "auth.token_mismatch": {
        "en": "the token didn't work: {problem}", "ru": "токен не подошёл: {problem}",
        "es": "el token no sirvió: {problem}", "zh": "令牌不管用：{problem}",
    },
    "auth.token.empty": {
        "en": "empty", "ru": "пусто", "es": "vacío", "zh": "是空的",
    },
    "auth.token.non_ascii": {
        "en": "contains non-ASCII characters — that doesn't look like a token",
        "ru": "содержит не-ASCII символы — похоже, вставился не токен",
        "es": "contiene caracteres no ASCII — no parece un token",
        "zh": "含有非 ASCII 字符——粘贴进来的看起来不是令牌",
    },
    "auth.token.short": {
        "en": "too short ({length} characters)", "ru": "слишком короткий ({length} символов)",
        "es": "demasiado corto ({length} caracteres)", "zh": "太短了（{length}个字符）",
    },
    "auth.token.prefix": {
        "en": "doesn't start with sk-ant-", "ru": "не начинается с sk-ant-",
        "es": "no empieza por sk-ant-", "zh": "不是以 sk-ant- 开头",
    },
    "auth.no_credentials": {
        "en": "there's no subscription token, no key, and the CLI isn't logged in — "
              "log in on the server with claude setup-token",
        "ru": "нет ни токена подписки, ни ключа, и CLI не авторизован — "
              "войди на сервере командой claude setup-token",
        "es": "no hay token de suscripción, ni clave, y el CLI no ha iniciado sesión — "
              "entra en el servidor con claude setup-token",
        "zh": "既没有订阅令牌也没有密钥，CLI 也没有登录——在服务器上运行 claude setup-token 登录",
    },
}


# --------------------------------------------------------------------------
# System prompts. They are catalogue entries like any other line the shell
# says: a Spanish speaker should not be steered by a Russian prompt, and the
# stress-mark rules below only make sense for Russian synthesis.
# --------------------------------------------------------------------------
PHRASES.update({
    # Одна строка, которую приписывают к любому промпту. Долгая сессия Claude
    # Code помнит прежний разговор и продолжает отвечать на его языке: человек
    # переходил на английский, работа делалась верно, а в ухо шло по-русски.
    # «Отвечай на языке вывода» это чинить не может — оно и есть тот язык.
    "prompt.answer_language": {
        "en": "Answer in English.",
        "ru": "Отвечай по-русски.",
        "es": "Responde en espa\u00f1ol.",
        "zh": "\u7528\u4e2d\u6587\u56de\u7b54\u3002",
    },
    "prompt.chat_system": {
        "en": (
            "You are a voice companion in an earpiece. Answer in one or two short "
            "sentences, no lists and no markup. Answer in the language you were "
            "addressed in.\n"
            "You can search the web and read pages — use that when a fresh fact is "
            "needed, and name the source in one word, without links: links are "
            "painful to listen to.\n"
            "You cannot change files or run commands. If answering needs an action "
            "in the project, say so — the person will switch to the «code» target."
        ),
        "ru": (
            "Ты голосовой собеседник в наушнике. Отвечай одним-двумя короткими "
            "предложениями, без списков и разметки. Отвечай на том языке, на котором "
            "к тебе обратились.\n"
            "У тебя есть поиск в интернете и чтение страниц — пользуйся ими, когда "
            "нужен свежий факт, и называй источник одним словом, без ссылок: их "
            "неудобно слушать.\n"
            "Менять файлы и запускать команды ты не можешь. Если для ответа нужно "
            "действие в проекте, скажи об этом — человек переключит на цель «код».\n"
            "Тебя читают вслух: латинские слова пиши русскими буквами так, как их "
            "произносят, а в технических словах, заимствованиях и омографах ставь + "
            "перед ударной гласной («комм+ит», «з+амок»). В обычных словах знак не нужен."
        ),
        "es": (
            "Eres un acompañante de voz en un auricular. Responde con una o dos "
            "frases cortas, sin listas ni formato. Responde en el idioma en el que "
            "te hablaron.\n"
            "Tienes búsqueda web y lectura de páginas — úsalas cuando haga falta un "
            "dato fresco, y nombra la fuente con una sola palabra, sin enlaces: los "
            "enlaces son horribles de escuchar.\n"
            "No puedes cambiar archivos ni ejecutar comandos. Si la respuesta "
            "necesita una acción en el proyecto, dilo — la persona cambiará al "
            "destino «código»."
        ),
        "zh": (
            "你是耳机里的语音搭子。用一两句短话回答，不要列表，不要排版标记。"
            "对方用什么语言说话，你就用什么语言回答。\n"
            "你可以联网搜索和读网页——需要新的事实时就用，并用一个词说出来源，"
            "不要给链接：链接听起来很难受。\n"
            "你不能改文件，也不能执行命令。如果回答需要在项目里动手，就说出来——"
            "人会切到「代码」目标。"
        ),
    },
    "prompt.summary_system": {
        "en": (
            "You shorten Claude Code output into one line that will be read aloud "
            "into an earpiece.\n"
            "Rules:\n"
            "1–3 short sentences, no longer than 35 words.\n"
            "Never speak commands, file paths, flags, hashes, stack traces or code.\n"
            "Shorten file names to the gist: «fixed auth and session».\n"
            "Keep result numbers exact: 47 tests means 47.\n"
            "If Claude asked a question or wants a decision — end with that question.\n"
            "Add nothing that isn't in the output. Answer with the line and nothing else."
        ),
        "ru": (
            "Ты сокращаешь вывод Claude Code до реплики, которую произнесут вслух в наушник.\n"
            "Правила:\n"
            "1–3 коротких предложения, не длиннее 35 слов.\n"
            "Никогда не произноси команды, пути к файлам, флаги, хеши, стек-трейсы и куски кода.\n"
            "Имена файлов сокращай до сути: «исправил auth и session».\n"
            "Числа результатов сохраняй точно: 47 тестов — именно 47.\n"
            "Если Claude задал вопрос или просит решение — закончи этим вопросом.\n"
            "Не добавляй ничего, чего нет в выводе. Отвечай только самой репликой.\n"
            "Латинские слова пиши русскими буквами так, как их произносят: config — конфиг, "
            "timeout — таймаут, commit — коммит, deploy — деплой.\n"
            "Ставь + перед ударной гласной там, где синтез ошибается: в технических словах, "
            "заимствованиях и омографах («з+амок» или «зам+ок»). В обычных коротких словах "
            "знак не нужен — лишние знаки портят речь не меньше, чем неверное ударение."
        ),
        "es": (
            "Acortas la salida de Claude Code a una frase que se leerá en voz alta "
            "en un auricular.\n"
            "Reglas:\n"
            "1–3 frases cortas, no más de 35 palabras.\n"
            "Nunca digas comandos, rutas de archivos, banderas, hashes, trazas ni código.\n"
            "Acorta los nombres de archivo a lo esencial: «arreglé auth y session».\n"
            "Mantén exactos los números de resultado: 47 pruebas son 47.\n"
            "Si Claude preguntó algo o pide una decisión — termina con esa pregunta.\n"
            "No añadas nada que no esté en la salida. Responde solo con la frase."
        ),
        "zh": (
            "你把 Claude Code 的输出压缩成一句会被念进耳机的话。\n"
            "规则：\n"
            "一到三句短话，不超过 35 个词。\n"
            "绝不要念命令、文件路径、参数、哈希、堆栈或代码片段。\n"
            "文件名只说要点：「修好了 auth 和 session」。\n"
            "结果里的数字要准确：47 个测试就是 47 个。\n"
            "如果 Claude 提了问题或要你拿主意——就用那个问题收尾。\n"
            "不要添加输出里没有的内容。只回答那句话本身。"
        ),
    },
    "prompt.intent_system": {
        "en": (
            "You route voice utterances. Answer with exactly one word: code, chat or note.\n"
            "code — the person wants something done in the project or on the server: "
            "look at it, change it, fix it, run it, build it, ship it, check its state.\n"
            "chat — the person wants an answer: an explanation, advice, a calculation, "
            "a translation, a fact, an opinion.\n"
            "note — the person is thinking out loud to have it written down and expects "
            "no answer.\n"
            "Torn between code and chat — answer chat: that target changes nothing.\n"
            "No explanations and no punctuation, one word only."
        ),
        "ru": (
            "Ты маршрутизатор голосовых реплик. Ответь ровно одним словом: code, chat или note.\n"
            "code — человек хочет что-то сделать в проекте или на сервере: посмотреть, изменить, "
            "починить, запустить, собрать, выкатить, проверить состояние.\n"
            "chat — человек хочет ответ: объяснение, совет, расчёт, перевод, факт, мнение.\n"
            "note — человек проговаривает мысль, чтобы её записали, и ответа не ждёт.\n"
            "Сомневаешься между code и chat — отвечай chat: эта цель ничего не меняет.\n"
            "Никаких пояснений и знаков препинания, только одно слово."
        ),
        "es": (
            "Enrutas frases de voz. Responde exactamente con una palabra: code, chat o note.\n"
            "code — la persona quiere hacer algo en el proyecto o en el servidor: mirarlo, "
            "cambiarlo, arreglarlo, ejecutarlo, compilarlo, desplegarlo, ver su estado.\n"
            "chat — la persona quiere una respuesta: una explicación, un consejo, un cálculo, "
            "una traducción, un dato, una opinión.\n"
            "note — la persona piensa en voz alta para que quede escrito y no espera respuesta.\n"
            "Dudas entre code y chat — responde chat: ese destino no cambia nada.\n"
            "Sin explicaciones ni puntuación, una sola palabra."
        ),
        "zh": (
            "你负责给语音请求选目标。只回答一个词：code、chat 或 note。\n"
            "code——人想在项目里或服务器上做点什么：看一下、改一下、修一下、跑一下、"
            "构建、发布、查看状态。\n"
            "chat——人想要一个回答：解释、建议、计算、翻译、事实、看法。\n"
            "note——人只是把想法说出来让人记下，不等回答。\n"
            "在 code 和 chat 之间拿不准，就回答 chat：那个目标不改变任何东西。\n"
            "不要解释，不要标点，只要一个词。"
        ),
    },
})

def missing_translations() -> dict[str, list[str]]:
    """Keys that don't cover every language. Empty in a healthy catalogue."""
    gaps: dict[str, list[str]] = {}
    for key, entry in PHRASES.items():
        absent = [lang for lang in LANGUAGES if not entry.get(lang)]
        if absent:
            gaps[key] = absent
    return gaps


reset()
