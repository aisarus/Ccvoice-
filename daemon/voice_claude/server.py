"""voice-claude-daemon: WebSocket server speaking the spec's protocol.

Implements the daemon side of spec section `protocol`. One long-lived Claude
Code session, a chat thread and a note inbox sit behind the router; the phone
sends speech segments and receives state, routes, summaries and approvals.
"""
from __future__ import annotations

import asyncio
import http
import json
import logging
import mimetypes
import os
import secrets
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from websockets.datastructures import Headers
from websockets.http11 import Response

from . import formatter, state
from .ambient import AmbientBuffer, RateLimiter, WhisperGate
from .auth import (SetupError, SetupTokenFlow, apply_token, credential_kind,
                   credential_problem, persist_token, token_problem)
from .router import Router
from .speaker import Decision, Features, SegmentContext, SpeakerClassifier, debug_record
from .spec import defaults, load_spec
from .targets import ChatTarget, CodeTarget, NoteTarget, TargetSet

log = logging.getLogger("voice-claude")
PROTOCOL_VERSION = 1
CLIENT_DIR = Path(__file__).resolve().parents[2] / "client" / "web"
EXTRA_TYPES = {".webmanifest": "application/manifest+json", ".svg": "image/svg+xml"}


@dataclass
class Settings:
    """One port serves both the client and the WebSocket: hosting platforms
    expose exactly one, and a same-origin wss:// keeps the browser happy."""

    workspace: str = "~"
    host: str = "0.0.0.0"
    port: int = 8787
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    note_path: str = "~/voice-claude/inbox.md"
    ambient_submode: str = "off"
    workspace_repo: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        """Read the deployment environment (Render, Fly, Koyeb, a plain VPS)."""
        return cls(
            workspace=os.environ.get("WORKSPACE_DIR", "/tmp/workspace"),
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", 8787)),
            token=os.environ.get("VOICE_TOKEN") or secrets.token_urlsafe(24),
            note_path=os.environ.get("NOTE_PATH", "/tmp/workspace/inbox.md"),
            ambient_submode=os.environ.get("AMBIENT_SUBMODE", "off"),
            workspace_repo=os.environ.get("WORKSPACE_REPO") or None,
        )


class Daemon:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.machine = state.Machine(on_change=self._on_state_change)
        self.classifier = SpeakerClassifier()
        self.router = Router()
        self.ambient = AmbientBuffer(submode=settings.ambient_submode)
        self.whisper = WhisperGate()
        self.proactive_limit = RateLimiter()
        self.targets = TargetSet(
            code=CodeTarget(settings.workspace, permission_hook=self._ask_permission),
            chat=ChatTarget(),
            note=NoteTarget(settings.note_path),
        )
        self.clients: set[Any] = set()
        self.telemetry: list[dict[str, Any]] = []
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._setup: SetupTokenFlow | None = None
        self._pending_tools: dict[str, str] = {}
        self.preapproved: set[str] = set()

    # -- transport -------------------------------------------------------
    async def handler(self, websocket: Any) -> None:
        self._loop = asyncio.get_running_loop()
        try:
            async for raw in websocket:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await self._send(websocket, {"id": "error", "code": "bad_json",
                                                 "message": "not JSON", "recoverable": True})
                    continue
                await self._dispatch(websocket, message)
        finally:
            self.clients.discard(websocket)

    async def _dispatch(self, ws: Any, msg: dict[str, Any]) -> None:
        kind = msg.get("id")
        if kind == "hello":
            if msg.get("token") != self.settings.token:
                await self._send(ws, {"id": "error", "code": "unauthorized",
                                      "message": "bad token", "recoverable": False})
                await ws.close()
                return
            self.clients.add(ws)
            await self._send(ws, {
                "id": "welcome", "v": PROTOCOL_VERSION,
                "targets": [t["id"] for t in load_spec()["targets"]["list"]],
                "active_session": str(Path(self.settings.workspace).expanduser()),
                "state": self.machine.state,
                "ambient": self.ambient.submode,
                "code_available": self.targets.code.available,
                "chat_available": self.targets.chat.available,
                "credential": credential_kind(),
                "credential_problem": credential_problem(),
            })
        elif kind == "speech_segment":
            await self._on_segment(ws, msg)
        elif kind == "permission_response":
            self._resolve_permission(msg)
        elif kind == "interrupt":
            await self._on_interrupt(ws, msg)
        elif kind == "barge_in":
            self.machine.to(state.LISTENING)
            await self._broadcast_state()
        elif kind == "ambient_control":
            if "submode" in msg:
                self.ambient.set_submode(msg["submode"])
            if "bystander_transcript" in msg:
                self.ambient.set_bystander_transcript(bool(msg["bystander_transcript"]))
            if msg.get("wipe"):
                self.ambient.wipe()
            await self._send(ws, {"id": "ambient_control", "submode": self.ambient.submode,
                                  "bystander_transcript": self.ambient.bystander_transcript,
                                  "lines": len(self.ambient.lines())})
        elif kind == "target_switch":
            try:
                self.router.force(msg.get("target"))
            except ValueError as exc:
                await self._send(ws, {"id": "error", "code": "unknown_target",
                                      "message": str(exc), "recoverable": True})
                return
            await self._send(ws, {"id": "route", "target": self.router.forced_target,
                                  "reason": "forced" if self.router.forced_target else "auto",
                                  "confidence": 1.0})
        elif kind in ("auth_start", "auth_code", "auth_set"):
            await self._on_auth(ws, msg)
        elif kind == "ping":
            await self._send(ws, {"id": "ping", "ts": int(time.time() * 1000)})
        else:
            await self._send(ws, {"id": "error", "code": "unknown_message",
                                  "message": str(kind), "recoverable": True})

    # -- the voice loop --------------------------------------------------
    async def _on_segment(self, ws: Any, msg: dict[str, Any]) -> None:
        text = (msg.get("transcript") or "").strip()
        decision = self._classify(msg)
        device = msg.get("device", "phone_mic")

        if decision.role == "self_echo" or not text:
            return
        self.ambient.add(decision.role, text, decision.confidence)

        if self._pending:
            await self._answer_permission_by_voice(ws, text, decision)
            return

        if not self.classifier.may("execute", decision):
            await self._send(ws, {"id": "route", "target": None, "reason": "role_gate",
                                  "role": decision.role, "confidence": round(decision.confidence, 3),
                                  "label": decision.label_ru})
            return

        if self.router.is_misroute_recovery(text):
            self.router.force(None)
            await self._send(ws, {"id": "route", "target": None, "reason": "misroute_recovery",
                                  "confidence": 1.0})
            return

        route = self.router.route(text, ms_since_last=self.machine.ms_since_window())
        await self._send(ws, {"id": "route", "target": route.target, "reason": route.reason,
                              "confidence": round(route.confidence, 3),
                              "earcon": self.router.earcon_for(route.target),
                              "role": decision.role, "label": decision.label_ru})

        self.machine.to(state.THINKING)
        await self._broadcast_state()

        preamble = "\n".join(load_spec()["command_passthrough"]["allowed_additions"]
                             ["system_preamble"].splitlines())
        role_line = self.classifier.role_line(decision, device)
        if route.target == "chat" and AmbientBuffer.is_recall(text) and self.ambient.enabled:
            role_line += "\n[ambient] последние реплики:\n" + self.ambient.transcript()

        reply = await self.targets[route.target].send(route.text, preamble, role_line)
        summary = await self._voice_summary(reply, route.target)

        self.machine.to(state.SPEAKING)
        self.machine.open_window()
        await self._broadcast({"id": "voice_summary", "text": summary.text,
                               "is_question": summary.is_question, "target": route.target,
                               "stubbed": reply.stubbed, "full_output": reply.full_output})
        await self._broadcast_state()

    async def _voice_summary(self, reply: Any, target: str) -> formatter.VoiceSummary:
        """Вслух идёт пересказ, а не вывод. Полный текст остаётся на экране."""
        if reply.stubbed or target == "note":
            return formatter.summarize(reply.text)
        if target == "chat":
            return formatter.summarize(reply.text, llm=lambda text: text)
        spoken = None
        if self.targets.summary is not None:
            spoken = await self.targets.summary.shorten(reply.text)
        if spoken:
            return formatter.summarize(reply.text, llm=lambda _: spoken)
        return formatter.summarize(reply.text)

    def _classify(self, msg: dict[str, Any]) -> Decision:
        raw = msg.get("features")
        if not raw:
            # Client without acoustic analysis: trust nothing, ask for master only
            # when it says so, but never above the approval confidence floor.
            role = msg.get("role", "unknown")
            return Decision(role, 1.0 if role == "master" else 0.0,
                            0.8 if role == "master" else 0.0, "client_provided")
        features = Features(
            level_rel_db=float(raw.get("level_rel_db", 0.0)),
            snr_db=float(raw.get("snr_db", 20.0)),
            drr_db=float(raw.get("drr_db", 6.0)),
            c50_db=float(raw.get("c50_db", 10.0)),
            hf_ratio_db=float(raw.get("hf_ratio_db", 0.0)),
            lf_proximity_db=float(raw.get("lf_proximity_db", 2.0)),
            voiceprint_similarity=raw.get("voiceprint_similarity"),
        )
        ctx = SegmentContext(
            device=msg.get("device", "phone_mic"),
            duration_ms=int(msg.get("duration_ms", 1200)),
            voiced_frames=int(msg.get("voiced_frames", 40)),
            overlap=bool(msg.get("overlap", False)),
            clipping_pct=float(msg.get("clipping_pct", 0.0)),
            echo_correlation=float(msg.get("echo_correlation", 0.0)),
            steady_level_s=float(msg.get("steady_level_s", 0.0)),
        )
        decision = self.classifier.classify(features, ctx)
        self.telemetry.append(debug_record(decision, features, ctx))
        del self.telemetry[:-200]
        return decision

    # -- подключение подписки --------------------------------------------
    async def _on_auth(self, ws: Any, msg: dict[str, Any]) -> None:
        """Проводит `claude setup-token` через телефон: ссылка -> код -> токен."""
        if ws not in self.clients:
            await self._send(ws, {"id": "error", "code": "unauthorized",
                                  "message": "нужен токен доступа", "recoverable": False})
            return
        try:
            # Токен уже на руках — вставили его прямо в приложении.
            if msg["id"] == "auth_set":
                token = str(msg.get("token", "")).strip()
                problem = token_problem(token)
                if problem:
                    raise SetupError(f"токен не подошёл: {problem}")
                await self._accept_token(token)
                return

            if msg["id"] == "auth_start":
                if self._setup is not None:
                    self._setup.close()
                self._setup = SetupTokenFlow(command=tuple(msg["command"])) \
                    if msg.get("command") else SetupTokenFlow()
                url = await self._setup.start()
                await self._send(ws, {"id": "auth_url", "url": url})
                return

            if self._setup is None:
                raise SetupError("флоу не запущен")
            token = await self._setup.submit(msg.get("code", ""))
            self._setup = None
            await self._accept_token(token)
        except (SetupError, FileNotFoundError, KeyError) as exc:
            if self._setup is not None:
                self._setup.close()
                self._setup = None
            await self._send(ws, {"id": "auth_error", "message": str(exc)})

    # -- approvals -------------------------------------------------------
    async def _ask_permission(self, tool_name: str, input_data: dict[str, Any]) -> bool:
        raw = f"{tool_name} {json.dumps(input_data, ensure_ascii=False)[:200]}"
        if tool_name in self.preapproved:
            return True
        request_id = secrets.token_hex(6)
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._pending_tools[request_id] = raw
        await self._broadcast({"id": "permission_request", "request_id": request_id,
                               "spoken": formatter.approval_to_speech(raw), "raw": raw,
                               "earcon": "needs_approval"})
        try:
            return await asyncio.wait_for(future, timeout=60)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(request_id, None)
            self._pending_tools.pop(request_id, None)

    async def _answer_permission_by_voice(self, ws: Any, text: str, decision: Decision) -> None:
        """Пока висит запрос разрешения, реплика — это ответ на него, а не команда."""
        action = formatter.parse_approval(text)
        if action is None:
            await self._send(ws, {"id": "route", "target": None, "reason": "awaiting_permission",
                                  "role": decision.role, "label": decision.label_ru})
            return
        if action == "speak_details":
            await self._broadcast({"id": "voice_summary", "text": self._pending_detail(),
                                   "is_question": True, "target": "approval", "stubbed": False,
                                   "full_output": self._pending_detail()})
            return
        if not self.classifier.may("approve", decision):
            await self._send(ws, {"id": "route", "target": None, "reason": "approval_role_gate",
                                  "role": decision.role, "confidence": round(decision.confidence, 3),
                                  "label": decision.label_ru})
            return

        request_id = next(iter(self._pending))
        self._resolve_permission({"request_id": request_id, "decision": action,
                                  "role": decision.role, "confidence": decision.confidence})
        approved = action.startswith("approve")
        if action == "approve_and_remember_rule":
            self.preapproved.add(self._pending_tools.get(request_id, ""))
        await self._broadcast({"id": "permission_result", "request_id": request_id,
                               "approved": approved, "remembered": action.endswith("rule"),
                               "earcon": "accepted" if approved else "error"})

    async def _accept_token(self, token: str) -> None:
        """Применить токен немедленно и сохранить, чтобы пережил перезапуск."""
        apply_token(token)
        persisted = persist_token(token)
        await self.targets.reset_sessions()
        await self._broadcast({"id": "auth_token", "token": token,
                               "credential": credential_kind(),
                               "code_available": self.targets.code.available,
                               "chat_available": self.targets.chat.available,
                               "persisted": persisted,
                               "persist_hint": "CLAUDE_CODE_OAUTH_TOKEN"})

    def _pending_detail(self) -> str:
        request_id = next(iter(self._pending), "")
        return self._pending_tools.get(request_id, "Нечего уточнять.")

    def _resolve_permission(self, msg: dict[str, Any]) -> None:
        future = self._pending.get(msg.get("request_id", ""))
        if future is None or future.done():
            return
        role = msg.get("role", "unknown")
        confidence = float(msg.get("confidence", 0.0))
        floor = load_spec()["speaker_identification"]["policy"]["permission_min_confidence"]
        if role != "master" or confidence < floor:
            log.warning("approval rejected: role=%s confidence=%.2f", role, confidence)
            future.set_result(False)
            return
        decision_name = msg.get("decision", "reject")
        if decision_name == "approve_and_remember_rule":
            self.preapproved.add(self._pending_tools.get(msg.get("request_id", ""), ""))
        future.set_result(decision_name.startswith("approve"))

    async def _on_interrupt(self, ws: Any, msg: dict[str, Any]) -> None:
        """«стоп» останавливает голос, «останови работу» — саму работу."""
        scope = msg.get("scope", "voice")
        if scope == "work":
            await self.targets.code.interrupt()
            await self._broadcast({"id": "voice_summary", "text": "Остановил.",
                                   "is_question": False, "target": "code",
                                   "stubbed": False, "full_output": "interrupt"})
        self.machine.to(state.LISTENING)
        self.machine.open_window()
        await self._broadcast_state()

    # -- plumbing --------------------------------------------------------
    def _on_state_change(self, previous: str, current: str) -> None:
        log.info("state %s -> %s", previous, current)

    async def _broadcast_state(self) -> None:
        await self._broadcast({"id": "state", "state": self.machine.state,
                               "window_open": self.machine.window_open()})

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        for ws in list(self.clients):
            await self._send(ws, payload)

    async def _send(self, ws: Any, payload: dict[str, Any]) -> None:
        try:
            await ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:  # client vanished mid-send
            log.debug("send failed: %s", exc)
            self.clients.discard(ws)


def http_response(status: http.HTTPStatus, body: bytes, content_type: str) -> Response:
    """Build the response by hand.

    `connection.respond()` already fills Content-Type and Content-Length, and
    assigning to `headers[...]` appends instead of replacing — that produced two
    conflicting Content-Length values, which a proxy rejects outright.
    """
    headers = Headers()
    headers["Content-Type"] = content_type
    headers["Content-Length"] = str(len(body))
    headers["Cache-Control"] = "no-store"
    return Response(status.value, status.phrase, headers, body)


def static_response(path: str, directory: Path = CLIENT_DIR) -> Response:
    """Serve the phone client from the same origin as the WebSocket."""
    if path in ("", "/"):
        path = "/index.html"
    target = (directory / path.lstrip("/")).resolve()
    if directory.resolve() not in target.parents or not target.is_file():
        return http_response(http.HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")
    body = target.read_bytes()
    content_type = EXTRA_TYPES.get(target.suffix) or \
        mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if content_type.startswith(("text/", "application/javascript")):
        content_type += "; charset=utf-8"
    return http_response(http.HTTPStatus.OK, body, content_type)


def make_process_request(directory: Path = CLIENT_DIR) -> Any:
    """HTTP side of the single port: health check, client, everything else 404."""

    async def process_request(connection: Any, request: Any) -> Any:
        path = request.path.split("?")[0]
        if path == "/healthz":
            return http_response(http.HTTPStatus.OK, b"ok\n", "text/plain; charset=utf-8")
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None
        return static_response(path, directory)

    return process_request


async def run(settings: Settings) -> None:
    from websockets.asyncio.server import serve

    await bootstrap_workspace(settings)
    daemon = Daemon(settings)
    process_request = make_process_request()

    # Health check стучится раз в секунду: без этого лог состоит из него одного.
    if not log.isEnabledFor(logging.DEBUG):
        logging.getLogger("websockets.server").setLevel(logging.WARNING)
    # SDK предупреждает, что для разрешённых списком инструментов колбэк не
    # вызывается. Так и задумано: список разрешает чтение, колбэк запрещает
    # остальное. В логе это только шум.
    warnings.filterwarnings("ignore", message=".*can_use_tool will not be invoked.*")

    print(f"voice-claude-daemon\n"
          f"  workspace : {Path(settings.workspace).expanduser()}\n"
          f"  listening : {settings.host}:{settings.port} (клиент и WebSocket на одном порту)\n"
          f"  token     : {settings.token}\n"
          f"  code      : {'ready' if daemon.targets.code.available else 'stub (нет SDK/ключа)'}\n"
          f"  chat      : {'ready' if daemon.targets.chat.available else 'stub (нет SDK/ключа)'}",
          flush=True)
    async with serve(daemon.handler, settings.host, settings.port, process_request=process_request):
        await asyncio.Future()


async def bootstrap_workspace(settings: Settings) -> None:
    """On a host with no checkout, clone the repo Claude Code will work in."""
    workspace = Path(settings.workspace).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    if not settings.workspace_repo or (workspace / ".git").exists():
        return
    url = settings.workspace_repo
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://") and "@" not in url:
        url = url.replace("https://", f"https://x-access-token:{token}@", 1)
    log.info("cloning workspace from %s", settings.workspace_repo)
    process = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "50", url, str(workspace),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await process.communicate()
    if process.returncode != 0:
        log.error("workspace clone failed: %s", stderr.decode()[:400])
