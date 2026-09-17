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

from .auth import credential_kind, credential_problem, credentials_present

PermissionHook = Callable[[str, dict[str, Any]], Awaitable[bool]]


@dataclass
class Reply:
    text: str
    full_output: str
    target: str
    stubbed: bool = False


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
        return "не установлен claude-agent-sdk"
    problem = credential_problem()
    if problem:
        return f"токен доступа неверный — {problem}"
    return "не подключена подписка Claude"


class _SdkTarget:
    """Общая часть: одна долгая сессия, которую не пересоздают на каждый запрос."""

    target_id = "sdk"

    def __init__(self, cwd: str | Path) -> None:
        self.cwd = Path(cwd).expanduser()
        self._client: Any | None = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return sdk_available()

    def _options(self) -> Any:  # pragma: no cover - переопределяется
        raise NotImplementedError

    async def connect(self) -> None:
        if self._client is not None or not self.available:
            return
        from claude_agent_sdk import ClaudeSDKClient  # type: ignore

        self.cwd.mkdir(parents=True, exist_ok=True)
        self._client = ClaudeSDKClient(options=self._options())
        await self._client.connect()

    async def send(self, text: str, preamble: str = "", role_line: str = "") -> Reply:
        message = "\n".join(part for part in (preamble, role_line, "", text) if part).strip()
        if not self.available:
            return Reply(text=f"Claude недоступен: {_stub_reason()}.",
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
            if self.permission_hook is None:
                return PermissionResultAllow()
            approved = await self.permission_hook(tool_name, input_data)
            return PermissionResultAllow() if approved else PermissionResultDeny(
                message="Отклонено голосом")

        return ClaudeAgentOptions(cwd=str(self.cwd), can_use_tool=can_use_tool)


class ChatTarget(_SdkTarget):
    """Разговорная цель: умеет искать в интернете и читать страницы, но не
    трогает файлы и оболочку. Поиск — это чтение, а не изменение мира, поэтому
    цель остаётся безопасным дефолтом роутера."""

    target_id = "chat"
    READ_ONLY_TOOLS = ("WebSearch", "WebFetch")

    def __init__(self, cwd: str | Path | None = None, model: str | None = None,
                 system: str | None = None) -> None:
        super().__init__(cwd or Path(tempfile.gettempdir()) / "voice-claude-chat")
        self.model = model
        self.system = system or (
            "Ты голосовой собеседник в наушнике. Отвечай одним-двумя короткими "
            "предложениями, без списков и разметки. Отвечай на том языке, на котором "
            "к тебе обратились.\n"
            "У тебя есть поиск в интернете и чтение страниц — пользуйся ими, когда "
            "нужен свежий факт, и называй источник одним словом, без ссылок: их "
            "неудобно слушать.\n"
            "Менять файлы и запускать команды ты не можешь. Если для ответа нужно "
            "действие в проекте, скажи об этом — человек переключит на цель «код»."
        )

    def _options(self) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions  # type: ignore

        async def can_use_tool(tool_name: str, input_data: dict[str, Any], _ctx: Any) -> Any:
            # Разрешение выдаём сами: интерактивного запроса здесь некому показать,
            # а список держим узким, чтобы цель осталась неспособной что-то менять.
            from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny  # type: ignore
            if tool_name in self.READ_ONLY_TOOLS:
                return PermissionResultAllow()
            return PermissionResultDeny(
                message=f"{tool_name} недоступен в разговорной цели — скажи «в код», если нужно действие"
            )

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
    SYSTEM = (
        "Ты сокращаешь вывод Claude Code до реплики, которую произнесут вслух в наушник.\n"
        "Правила:\n"
        "1–3 коротких предложения, не длиннее 35 слов.\n"
        "Никогда не произноси команды, пути к файлам, флаги, хеши, стек-трейсы и куски кода.\n"
        "Имена файлов сокращай до сути: «исправил auth и session».\n"
        "Числа результатов сохраняй точно: 47 тестов — именно 47.\n"
        "Если Claude задал вопрос или просит решение — закончи этим вопросом.\n"
        "Не добавляй ничего, чего нет в выводе. Отвечай только самой репликой.\n"
        "Отвечай на языке вывода: русский вывод — русская реплика, английский — английская."
    )

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
        return Reply(text="Записал.", full_output=text, target="note")


@dataclass
class TargetSet:
    code: CodeTarget
    chat: ChatTarget
    note: NoteTarget
    summary: "SummaryTarget | None" = None
    _by_id: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.summary = self.summary or SummaryTarget()
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
