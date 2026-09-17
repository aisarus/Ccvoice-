"""Мост в Telegram: «скинь мне в телегу».

Первый выход наружу, поэтому узкий нарочно. Отправлять можно только в один
заранее заданный чат — свой. Голос слышат все, кто рядом, и мост, способный
слать куда попало, стал бы способом вынести файлы из машины чужими руками.

Токен бота живёт в файле службы, рядом с остальными секретами, и в репозиторий
не попадает никогда.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path

API = "https://api.telegram.org"
# У ботов потолок 50 МБ; берём с запасом, чтобы ошибка была наша и понятная.
MAX_BYTES = 45 * 1024 * 1024

# То, что не уходит наружу голосом ни при каких условиях. Сказать «скинь
# конфиг» легко, а вынести вместе с ним ключи — необратимо.
SECRET_NAMES = re.compile(
    r"(^|/)\.env|\.pem$|\.key$|id_rsa|\.credentials|credentials\.json$|"
    r"secret|token|password|\.p12$|\.keystore$|\.jks$", re.I)

# Токен бота выглядит как «1234567890:ABC-DEF…». Проверяем форму заранее:
# иначе непечатаемый или скопированный с мусором токен ломает сборку адреса,
# и человек слышит «ответил непонятным» вместо «токен не тот».
TOKEN_SHAPE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")

PHRASES = (
    "скинь в телегу", "скинь мне в телегу", "скинь в телеграм", "скинь мне в телеграм",
    "отправь в телегу", "отправь в телеграм", "пришли в телегу", "пришли в телеграм",
    "кинь в телегу", "кинь в телеграм",
)


def configured() -> bool:
    return bool(os.environ.get("TELEGRAM_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def share_request(text: str) -> str | None:
    """«Скинь мне в телегу конфиг» → «конфиг». Пустая строка — значит «то,
    над чем мы только что работали»."""
    lowered = " ".join(text.lower().split())
    for phrase in PHRASES:
        if phrase in lowered:
            rest = lowered.split(phrase, 1)[1]
            return re.sub(r"^[\s,.:—-]+", "", rest).strip()
    return None


def refuse_reason(path: Path, workspace: Path) -> str | None:
    """Почему этот файл наружу не уйдёт. None — можно отправлять."""
    try:
        real = path.resolve()
        root = workspace.resolve()
    except OSError:
        return "файл не читается"
    if not real.is_file():
        return "такого файла нет"
    if root not in real.parents and real != root:
        return "файл вне рабочего каталога"
    if SECRET_NAMES.search(str(real)):
        return "похоже на секрет — такое наружу не отправляю"
    size = real.stat().st_size
    if size > MAX_BYTES:
        return f"слишком большой: {size // (1024 * 1024)} МБ"
    if size == 0:
        return "файл пустой"
    return None


class Bridge:
    """Тонкая обёртка над Bot API. Без зависимостей: ставить ради двух
    запросов целую библиотеку незачем."""

    def __init__(self, token: str = "", chat_id: str = "", api: str = "") -> None:
        self.token = token or os.environ.get("TELEGRAM_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        # Адрес вынесен наружу не ради красоты: так мост проверяется
        # подставным сервером, и так же он работает за прокси.
        self.api = (api or os.environ.get("TELEGRAM_API") or API).rstrip("/")

    @property
    def ready(self) -> bool:
        return bool(self.token and self.chat_id)

    def token_problem(self) -> str | None:
        if not self.token:
            return "телеграм не настроен"
        if not TOKEN_SHAPE.match(self.token):
            return "токен бота не похож на настоящий — он вида 1234567890:ABC…"
        if not str(self.chat_id).lstrip("-").isdigit():
            return "номер чата должен быть числом"
        return None

    def _url(self, method: str) -> str:
        return f"{self.api}/bot{self.token}/{method}"

    def send_text(self, text: str) -> str | None:
        """None — отправлено, строка — человеческая причина отказа."""
        problem = self.token_problem()
        if problem:
            return problem
        data = json.dumps({"chat_id": self.chat_id, "text": text[:4000]}).encode("utf-8")
        request = urllib.request.Request(self._url("sendMessage"), data=data,
                                         headers={"Content-Type": "application/json"})
        return self._call(request)

    def send_document(self, path: Path, caption: str = "") -> str | None:
        problem = self.token_problem()
        if problem:
            return problem
        boundary = uuid.uuid4().hex
        body = bytearray()

        def field(name: str, value: str) -> None:
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(f"{value}\r\n".encode())

        field("chat_id", self.chat_id)
        if caption:
            field("caption", caption[:1000])
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="document"; filename="{path.name}"\r\n'
            f"Content-Type: {kind}\r\n\r\n".encode())
        body.extend(path.read_bytes())
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode())

        request = urllib.request.Request(
            self._url("sendDocument"), data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        return self._call(request)

    def _call(self, request: urllib.request.Request) -> str | None:
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                answer = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace")).get("description", "")
            except Exception:                       # ответ мог быть и не json
                pass
            if exc.code == 401:
                return "телеграм не принял токен бота"
            if exc.code == 400 and "chat not found" in detail.lower():
                return "телеграм не знает такого чата — напиши боту первым"
            return f"телеграм отказал: {detail or exc.code}"
        except (urllib.error.URLError, OSError, TimeoutError):
            return "до телеграма не достучаться"
        except ValueError:
            return "телеграм ответил непонятным"
        return None if answer.get("ok") else f"телеграм отказал: {answer.get('description', '')}"

# -- «конфиг» против config.json -------------------------------------------
#
# Человек говорит по-русски, а файлы называются латиницей. Точного совпадения
# не будет никогда: «скинь конфиг» — это config.json, «аус ти эс» — auth.ts.
# Поэтому сравниваем по звучанию: переводим сказанное в латиницу и ищем
# ближайшее имя. Это дёшево, предсказуемо и не требует круга к модели.

ЗВУКИ = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", " ": "",
}


def latin(word: str) -> str:
    """«конфиг» → «konfig». Для слов, уже написанных латиницей, — как есть."""
    return "".join(ЗВУКИ.get(ch, ch) for ch in word.lower())


def best_match(spoken: str, names: "list[str]") -> str | None:
    """Ближайшее имя файла к сказанному, или None, если ничего не похоже."""
    import difflib

    if not spoken or not names:
        return None
    искомое = latin(spoken)
    таблица = {latin(Path(n).stem): n for n in names}
    # Сначала точное вхождение: «лог» найдёт logger.py раньше, чем похожее.
    for стем, имя in таблица.items():
        if искомое and (искомое == стем or искомое in стем):
            return имя
    близкие = difflib.get_close_matches(искомое, list(таблица), n=1, cutoff=0.7)
    if близкие:
        return таблица[близкие[0]]
    # Гласные в услышанном слове гуляют сильнее всего: «мейн» против main,
    # «ридми» против readme. Сравниваем костяк из согласных — но только
    # если он указывает ровно на один файл: отправить наружу не тот хуже,
    # чем не отправить ничего.
    цель = skeleton(искомое)
    совпали = {имя for стем, имя in таблица.items() if цель and skeleton(стем) == цель}
    return совпали.pop() if len(совпали) == 1 else None


def skeleton(word: str) -> str:
    """Костяк слова: согласные, без разницы между c и k, th и t."""
    плоско = word.replace("ph", "f").replace("th", "t").replace("ck", "k")
    return "".join(("k" if ch == "c" else ch) for ch in плоско
                   if ch.isalpha() and ch not in "aeiouy")
