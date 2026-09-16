"""Backends behind the routing targets.

`code` drives one long-lived Claude Code session through the Claude Agent SDK,
`chat` is a separate conversational thread on the Messages API, `note` appends
to a local inbox. Each backend degrades to an offline stub so the whole voice
loop can be exercised without keys — the stub says so instead of pretending.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

PermissionHook = Callable[[str, dict[str, Any]], Awaitable[bool]]


@dataclass
class Reply:
    text: str
    full_output: str
    target: str
    stubbed: bool = False


class CodeTarget:
    """One persistent Claude Code session. Never spawned per request."""

    def __init__(self, workspace: str | Path, permission_hook: PermissionHook | None = None) -> None:
        self.workspace = Path(workspace).expanduser()
        self.permission_hook = permission_hook
        self._client: Any | None = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            return False
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))

    async def connect(self) -> None:
        if self._client is not None or not self.available:
            return
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient  # type: ignore

        async def can_use_tool(tool_name: str, input_data: dict[str, Any], _ctx: Any) -> Any:
            from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny  # type: ignore
            if self.permission_hook is None:
                return PermissionResultAllow()
            approved = await self.permission_hook(tool_name, input_data)
            return PermissionResultAllow() if approved else PermissionResultDeny(message="Отклонено голосом")

        options = ClaudeAgentOptions(cwd=str(self.workspace), can_use_tool=can_use_tool)
        self._client = ClaudeSDKClient(options=options)
        await self._client.connect()

    async def send(self, text: str, preamble: str = "", role_line: str = "") -> Reply:
        message = "\n".join(p for p in (preamble, role_line, "", text) if p is not None).strip()
        if not self.available:
            return Reply(
                text="Claude Code недоступен: нет claude-agent-sdk или ключа.",
                full_output=message, target="code", stubbed=True,
            )
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
        return Reply(text=full, full_output=full, target="code")

    async def interrupt(self) -> None:
        if self._client is not None:
            await self._client.interrupt()


class ChatTarget:
    """Conversational Claude. Non-mutating, so it is the safe default route."""

    def __init__(self, model: str = "claude-sonnet-5", system: str | None = None) -> None:
        self.model = model
        self.system = system or (
            "Ты голосовой собеседник в наушнике. Отвечай одним-двумя короткими "
            "предложениями, без списков и разметки."
        )
        self.history: list[dict[str, str]] = []

    @property
    def available(self) -> bool:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    async def send(self, text: str, preamble: str = "", role_line: str = "") -> Reply:
        if not self.available:
            return Reply(text="Чат недоступен: нет anthropic SDK или ключа.",
                         full_output=text, target="chat", stubbed=True)
        import anthropic  # type: ignore

        client = anthropic.AsyncAnthropic()
        self.history.append({"role": "user", "content": "\n".join(p for p in (role_line, text) if p)})
        response = await client.messages.create(
            model=self.model, max_tokens=300, system=self.system, messages=self.history,
        )
        spoken = "".join(block.text for block in response.content if block.type == "text")
        self.history.append({"role": "assistant", "content": spoken})
        del self.history[:-20]
        return Reply(text=spoken, full_output=spoken, target="chat")


class NoteTarget:
    """Capture a thought without an answer."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    available = True

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
    _by_id: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._by_id = {"code": self.code, "chat": self.chat, "note": self.note}

    def __getitem__(self, target: str) -> Any:
        return self._by_id[target]
