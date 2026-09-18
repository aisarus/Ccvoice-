"""Бэкенды целей роутинга.

Обе разговорные цели ходят через Claude Agent SDK, поэтому годится и подписка
Claude (`CLAUDE_CODE_OAUTH_TOKEN`), и API-ключ: отдельный ключ с потокенной
оплатой не обязателен. `code` — долгая сессия с инструментами и голосовыми
подтверждениями, `chat` — та же авторизация, но инструменты выключены, поэтому
цель ничего не может изменить. `note` пишет в локальный инбокс.
"""
from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import os

from . import policy
from .auth import credential_kind, credential_problem, credentials_present
from . import i18n
from .i18n import t

PermissionHook = Callable[[str, dict[str, Any]], Awaitable[bool]]


@dataclass
class Reply:
    text: str
    full_output: str
    target: str
    stubbed: bool = False


def running_as_root() -> bool:
    """Служба под systemd обычно работает от root.

    CLI отказывается запускаться от root с выключенными разрешениями — и это
    правильная защита. Поведение «ничего не спрашивать» мы в этом случае даём
    колбэком: результат тот же, а сессия поднимается.
    """
    return hasattr(os, "geteuid") and os.geteuid() == 0


def sdk_available() -> bool:
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False
    return credentials_present()


def _stub_reason() -> str:
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return t("stub.no_sdk")
    problem = credential_problem()
    if problem:
        return t("stub.bad_token", problem=problem)
    return t("stub.no_subscription")


class _SdkTarget:
    """Общая часть: одна долгая сессия, которую не пересоздают на каждый запрос."""

    target_id = "sdk"

    #: Системный промпт цели меняется вместе с языком разговора.
    prompt_follows_language = False

    def __init__(self, cwd: str | Path) -> None:
        self.cwd = Path(cwd).expanduser()
        self._client: Any | None = None
        self._lock = asyncio.Lock()
        self._session_language: str | None = None

    @property
    def available(self) -> bool:
        return sdk_available()

    def _options(self) -> Any:  # pragma: no cover - переопределяется
        raise NotImplementedError

    async def connect(self) -> None:
        # Системный промпт задаётся один раз, при подъёме сессии, и живёт
        # столько же, сколько она. Человек, перешедший на другой язык,
        # получал верную работу и ответ вслух на прежнем: сессия всё ещё
        # держала промпт, с которым её подняли. Поэтому смена языка —
        # это повод поднять её заново, и только она.
        if (self._client is not None and self.prompt_follows_language
                and self._session_language != i18n.current()):
            await self.reset()
        if self._client is not None or not self.available:
            return
        from claude_agent_sdk import ClaudeSDKClient  # type: ignore

        self.cwd.mkdir(parents=True, exist_ok=True)
        self._client = ClaudeSDKClient(options=self._options())
        await self._client.connect()
        self._session_language = i18n.current()

    @staticmethod
    def compose(text: str, preamble: str = "", role_line: str = "") -> str:
        """Служебные строки, пустая строка, реплика человека.

        Пустую строку отфильтровывало вместе с пустыми служебными, и реплика
        прилипала к метаданным. На живом прогоне Claude отвечал на это
        «похоже, это системное сообщение об окружении, а не задача от вас» —
        то есть человека попросту не было слышно. Формат задан в спеке:
        `speaker_identification.claude_injection.example.sent_to_claude`.
        """
        head = "\n".join(part for part in (preamble, role_line) if part)
        return f"{head}\n\n{text}".strip() if head else text.strip()

    async def send(self, text: str, preamble: str = "", role_line: str = "") -> Reply:
        message = self.compose(text, preamble, role_line)
        if not self.available:
            return Reply(text=t("target.unavailable", reason=_stub_reason()),
                         full_output=message, target=self.target_id, stubbed=True)
        await self.connect()
        async with self._lock:
            assert self._client is not None
            await self._client.query(message)
            chunks: list[str] = []
            async for msg in self._client.receive_response():
                for block in getattr(msg, "content", []) or []:
                    chunk = getattr(block, "text", None)
                    if chunk:
                        chunks.append(chunk)
        full = "\n".join(chunks)
        return Reply(text=full, full_output=full, target=self.target_id)

    async def interrupt(self) -> None:
        if self._client is not None:
            await self._client.interrupt()

    async def reset(self) -> None:
        """Сбросить клиент — например, после того как подключили подписку."""
        client, self._client = self._client, None
        self._session_language = None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # pragma: no cover - клиент мог уже умереть
                pass


class CodeTarget(_SdkTarget):
    """Одна долгая Claude Code-сессия. Никогда не поднимается на каждый запрос."""

    target_id = "code"

    def __init__(self, workspace: str | Path, permission_hook: PermissionHook | None = None) -> None:
        super().__init__(workspace)
        self.permission_hook = permission_hook

    @property
    def workspace(self) -> Path:
        return self.cwd

    def _options(self) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions  # type: ignore

        async def can_use_tool(tool_name: str, input_data: dict[str, Any], _ctx: Any) -> Any:
            from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny  # type: ignore
            # Хозяин машины сказал «авто» — не спрашиваем ничего. Под root это
            # единственный способ: CLI не принимает там режим «ничего не
            # спрашивать», и без колбэка сессия просто не поднимется.
            if policy.mode() == "auto":
                return PermissionResultAllow()
            if self.permission_hook is None:
                return PermissionResultAllow()
            approved = await self.permission_hook(tool_name, input_data)
            return PermissionResultAllow() if approved else PermissionResultDeny(
                message=t("target.denied_by_voice"))

        options: dict[str, Any] = {"cwd": str(self.cwd)}
        if policy.mode() == "auto" and not running_as_root():
            # Ничего не спрашиваем: так решил хозяин машины.
            options["permission_mode"] = "bypassPermissions"
        else:
            options["permission_mode"] = "acceptEdits"
            options["can_use_tool"] = can_use_tool
        return ClaudeAgentOptions(**options)


class ChatTarget(_SdkTarget):
    """Разговорная цель: умеет искать в интернете и читать страницы, но не
    трогает файлы и оболочку. Поиск — это чтение, а не изменение мира, поэтому
    цель остаётся безопасным дефолтом роутера."""

    target_id = "chat"
    prompt_follows_language = True
    READ_ONLY_TOOLS = ("WebSearch", "WebFetch")

    def __init__(self, cwd: str | Path | None = None, model: str | None = None,
                 system: str | None = None) -> None:
        super().__init__(cwd or Path(tempfile.gettempdir()) / "voice-claude-chat")
        self.model = model
        # None — брать из каталога на языке разговора в момент подключения:
        # цель живёт долго, а язык решается на каждой реплике.
        self._system = system

    @property
    def system(self) -> str:
        return self._system or f'{t("prompt.chat_system")}\n{t("prompt.answer_language")}'

    def _options(self) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions  # type: ignore

        async def can_use_tool(tool_name: str, input_data: dict[str, Any], _ctx: Any) -> Any:
            """Отказ всему, кроме чтения мира.

            Два разрешённых инструмента SDK одобряет сам по списку и сюда не
            заходит — колбэк нужен для всех остальных: без него запрос на
            разрешение повис бы, показать его здесь некому.
            """
            from claude_agent_sdk import PermissionResultDeny  # type: ignore
            return PermissionResultDeny(
                message=t("target.chat_cannot", tool=tool_name))

        options: dict[str, Any] = {
            "cwd": str(self.cwd),
            "system_prompt": self.system,
            "allowed_tools": list(self.READ_ONLY_TOOLS),
            "can_use_tool": can_use_tool,
            # Поиску нужно несколько шагов: запрос, чтение, ответ.
            "max_turns": 6,
        }
        if self.model:
            options["model"] = self.model
        return ClaudeAgentOptions(**options)


class SummaryTarget(_SdkTarget):
    """Пересказ вывода Claude Code для чтения вслух.

    Спека (`voice_formatter`): в ухо идут 1–3 предложения, полный вывод остаётся
    на экране. Инструменты выключены — эта цель только сокращает текст.
    """

    target_id = "summary"
    prompt_follows_language = True
    @property
    def SYSTEM(self) -> str:                      # noqa: N802 - имя из спеки
        return f'{t("prompt.summary_system")}\n{t("prompt.answer_language")}'

    def __init__(self, cwd: str | Path | None = None) -> None:
        super().__init__(cwd or Path(tempfile.gettempdir()) / "voice-claude-summary")

    def _options(self) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions  # type: ignore

        return ClaudeAgentOptions(cwd=str(self.cwd), system_prompt=self.SYSTEM,
                                  allowed_tools=[], max_turns=1)

    async def shorten(self, output: str, timeout: float = 10.0) -> str | None:
        """Возвращает реплику для озвучки или None — тогда работают правила."""
        if not self.available or not output.strip():
            return None
        try:
            reply = await asyncio.wait_for(self.send(output), timeout=timeout)
        except (asyncio.TimeoutError, Exception):  # pragma: no cover - сеть/SDK
            return None
        spoken = reply.text.strip()
        return spoken or None


class IntentTarget(_SdkTarget):
    """Маршрут по смыслу задачи, а не по словарю.

    Словарь — это и есть кодовые слова: он ловит «git» и «тест», но живая речь
    ими не пользуется. Эта цель видит реплику до маршрутизации и отвечает одним
    словом. Инструментов у неё нет, и ответ её ни на что не влияет, кроме
    выбора цели.
    """

    target_id = "intent"
    prompt_follows_language = True
    VALID = ("code", "chat", "note")
    @property
    def SYSTEM(self) -> str:                      # noqa: N802 - имя из спеки
        return t("prompt.intent_system")

    def __init__(self, cwd: str | Path | None = None) -> None:
        super().__init__(cwd or Path(tempfile.gettempdir()) / "voice-claude-intent")

    def _options(self) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions  # type: ignore

        return ClaudeAgentOptions(cwd=str(self.cwd), system_prompt=self.SYSTEM,
                                  allowed_tools=[], max_turns=1)

    async def classify(self, text: str, timeout: float = 2.5) -> str | None:
        """Цель или None — тогда остаётся решение словаря."""
        if not self.available or not text.strip():
            return None
        try:
            reply = await asyncio.wait_for(self.send(text), timeout=timeout)
        except (asyncio.TimeoutError, Exception):  # pragma: no cover - сеть/SDK
            return None
        word = reply.text.strip().lower().strip(".,!?:;\"'«»").split(" ")[0]
        return word if word in self.VALID else None


class NoteTarget:
    """Захват мысли без ответа."""

    target_id = "note"
    available = True

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    async def send(self, text: str, preamble: str = "", role_line: str = "") -> Reply:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(f"- {text}\n")
        return Reply(text=t("note.saved"), full_output=text, target="note")


@dataclass
class TargetSet:
    code: CodeTarget
    chat: ChatTarget
    note: NoteTarget
    summary: "SummaryTarget | None" = None
    intent: "IntentTarget | None" = None
    _by_id: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.summary = self.summary or SummaryTarget()
        self.intent = self.intent or IntentTarget()
        self._by_id = {"code": self.code, "chat": self.chat, "note": self.note}

    def __getitem__(self, target: str) -> Any:
        return self._by_id[target]

    @property
    def credential_kind(self) -> str:
        return credential_kind()

    async def reset_sessions(self) -> None:
        await self.code.reset()
        await self.chat.reset()
        if self.summary is not None:
            await self.summary.reset()
        if self.intent is not None:
            await self.intent.reset()
