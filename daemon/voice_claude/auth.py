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
from dataclasses import dataclass

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[()][B0]")
URL_RE = re.compile(r"https://[^\s\x1b\x07\]]+oauth/authorize[^\s\x1b\x07\]]*")
TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")

SETUP_COMMAND = ("claude", "setup-token")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


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
        self._buffer = ""

    # -- публичный API ---------------------------------------------------
    async def start(self) -> str:
        """Запускает команду и возвращает OAuth-ссылку для телефона."""
        await asyncio.to_thread(self._spawn)
        text = await asyncio.to_thread(self._read_until, URL_RE, self.url_timeout_s)
        match = URL_RE.search(text)
        if not match:
            self.close()
            raise SetupError("не удалось получить ссылку авторизации")
        return match.group(0)

    async def submit(self, code: str) -> str:
        """Отдаёт код команде и возвращает долгоживущий токен."""
        if self._master is None:
            raise SetupError("флоу не запущен")
        await asyncio.to_thread(os.write, self._master, code.strip().encode() + b"\r")
        text = await asyncio.to_thread(self._read_until, TOKEN_RE, self.token_timeout_s)
        match = TOKEN_RE.search(text)
        if not match:
            self.close()
            raise SetupError("код не принят или токен не выдан")
        token = match.group(0)
        self.close()
        return token

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
        self._buffer = ""

    def _read_until(self, pattern: re.Pattern[str], timeout_s: float) -> str:
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
            self._buffer += strip_ansi(chunk.decode("utf-8", "replace"))
            if pattern.search(self._buffer):
                return self._buffer
        return self._buffer


def credentials_present() -> bool:
    """Подписка (OAuth-токен) или API-ключ — годится любое."""
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"))


def credential_kind() -> str:
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "subscription"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api_key"
    return "none"


def apply_token(token: str) -> None:
    """Токен начинает действовать сразу, до перезапуска сервиса."""
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
    os.environ.pop("ANTHROPIC_API_KEY", None)
