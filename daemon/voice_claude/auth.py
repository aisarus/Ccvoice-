"""Подключение подписки Claude вместо API-ключа.

`claude setup-token` — интерактивная команда: печатает OAuth-ссылку, ждёт код,
выдаёт долгоживущий токен. На хостинге терминала нет, поэтому флоу проводится
через сам голосовой интерфейс: телефон получает ссылку, возвращает код и
забирает токен.
"""
from __future__ import annotations

import asyncio
import os
import pty
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[()][B0]")
# Ссылка в терминале печатается с переносами, поэтому видимый текст обрезан.
# Целая ссылка лежит в OSC 8 — гиперссылке, которой терминал оборачивает вывод.
OSC8_RE = re.compile(r"\x1b\]8;[^;]*;(https://[^\x1b\x07]+)")
URL_RE = re.compile(r"https://[^\s\x1b\x07\]]+oauth/authorize[^\s\x1b\x07\]]*")
TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")
# Без этих параметров ссылка бесполезна: claude.ai ответит Invalid OAuth Request.
REQUIRED_PARAMS = ("redirect_uri", "code_challenge", "state")

SETUP_COMMAND = ("claude", "setup-token")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _unwrap_candidates(text: str) -> list[str]:
    """Склеивает ссылку, разорванную переносами строк."""
    joined, buffer = [], ""
    for line in text.splitlines():
        stripped = line.strip()
        if buffer:
            if stripped and " " not in stripped:
                buffer += stripped
                continue
            joined.append(buffer)
            buffer = ""
        match = URL_RE.search(stripped)
        if match and stripped.endswith(match.group(0)):
            buffer = match.group(0)      # строка кончилась ссылкой — возможно, перенос
        elif match:
            joined.append(match.group(0))
    if buffer:
        joined.append(buffer)
    return joined


def extract_auth_url(raw: str) -> str | None:
    """Достаёт полную ссылку авторизации из вывода терминала."""
    candidates = OSC8_RE.findall(raw)
    candidates += _unwrap_candidates(strip_ansi(raw))
    complete = [url for url in candidates
                if "oauth/authorize" in url and all(p in url for p in REQUIRED_PARAMS)]
    return max(complete, key=len) if complete else None


class SetupError(RuntimeError):
    pass


@dataclass
class SetupTokenFlow:
    """Гоняет интерактивную команду в pty и вытаскивает ссылку и токен."""

    command: tuple[str, ...] = SETUP_COMMAND
    url_timeout_s: float = 45.0
    token_timeout_s: float = 90.0

    def __post_init__(self) -> None:
        self._master: int | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._raw = ""

    # -- публичный API ---------------------------------------------------
    async def start(self) -> str:
        """Запускает команду и возвращает OAuth-ссылку для телефона."""
        await asyncio.to_thread(self._spawn)
        raw = await asyncio.to_thread(
            self._read_until, lambda text: extract_auth_url(text) is not None, self.url_timeout_s)
        url = extract_auth_url(raw)
        if url is None:
            self.close()
            raise SetupError("не удалось получить ссылку авторизации")
        return url

    async def submit(self, code: str) -> str:
        """Отдаёт код команде и возвращает долгоживущий токен."""
        if self._master is None:
            raise SetupError("флоу не запущен")
        await asyncio.to_thread(os.write, self._master, code.strip().encode() + b"\r")
        raw = await asyncio.to_thread(
            self._read_until, lambda text: TOKEN_RE.search(strip_ansi(text)) is not None,
            self.token_timeout_s)
        match = TOKEN_RE.search(strip_ansi(raw))
        if not match:
            detail = self._tail(raw)
            self.close()
            raise SetupError(f"код не принят или токен не выдан. CLI ответил: {detail}"
                             if detail else "код не принят или токен не выдан")
        token = match.group(0)
        self.close()
        return token

    @staticmethod
    def _tail(raw: str, limit: int = 220) -> str:
        """Последние осмысленные строки вывода — чтобы не гадать, что пошло не так."""
        lines = [line.strip() for line in strip_ansi(raw).splitlines() if line.strip()]
        skip = ("paste code here", "welcome", "browser didn", "hold shift", "this will guide")
        useful = [line for line in lines if not any(s in line.lower() for s in skip)]
        return " / ".join(useful[-3:])[:limit]

    def close(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.kill()
        if self._master is not None:
            try:
                os.close(self._master)
            except OSError:
                pass
        self._master, self._process = None, None

    # -- внутренности ----------------------------------------------------
    def _spawn(self) -> None:
        master, slave = pty.openpty()
        env = dict(os.environ, TERM="xterm-256color")
        # Ключ API заставил бы CLI пойти другим путём — здесь нужна подписка.
        env.pop("ANTHROPIC_API_KEY", None)
        self._process = subprocess.Popen(
            list(self.command), stdin=slave, stdout=slave, stderr=slave,
            env=env, start_new_session=True, close_fds=True,
        )
        os.close(slave)
        self._master = master
        self._raw = ""

    def _read_until(self, done: "Callable[[str], bool]", timeout_s: float) -> str:
        import select

        assert self._master is not None
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self._master], [], [], 0.4)
            if not ready:
                if self._process is not None and self._process.poll() is not None:
                    break
                continue
            try:
                chunk = os.read(self._master, 8192)
            except OSError:
                break
            if not chunk:
                break
            self._raw += chunk.decode("utf-8", "replace")
            if done(self._raw):
                return self._raw
        return self._raw


def token_problem(value: str) -> str | None:
    """Что не так с токеном. None — всё в порядке.

    Пустая проверка «переменная не пуста» врёт: в неё легко попадает обрывок
    приглашения или русский текст, и демон рапортует о готовности, пока CLI
    отвечает «Invalid auth token».
    """
    token = value.strip()
    if not token:
        return "пусто"
    if not token.isascii():
        return "содержит не-ASCII символы — похоже, вставился не токен"
    if len(token) < 20:
        return f"слишком короткий ({len(token)} символов)"
    if not token.startswith("sk-ant-"):
        return "не начинается с sk-ant-"
    return None


def credentials_present() -> bool:
    """Подписка (OAuth-токен) или API-ключ — годится любое, но настоящее."""
    return credential_kind() != "none"


def credential_kind() -> str:
    oauth = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if oauth and token_problem(oauth) is None:
        return "subscription"
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key and token_problem(key) is None:
        return "api_key"
    return "none"


def credential_problem() -> str | None:
    """Человеческая причина, почему доступа нет."""
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
        value = os.environ.get(name, "")
        if value:
            problem = token_problem(value)
            if problem:
                return f"{name}: {problem}"
    return None


def apply_token(token: str) -> None:
    """Токен начинает действовать сразу, до перезапуска сервиса."""
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
    os.environ.pop("ANTHROPIC_API_KEY", None)
