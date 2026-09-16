"""voice-claude-daemon: WebSocket server speaking the spec's protocol.

Implements the daemon side of spec section `protocol`. One long-lived Claude
Code session, a chat thread and a note inbox sit behind the router; the phone
sends speech segments and receives state, routes, summaries and approvals.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import formatter, state
from .ambient import AmbientBuffer, RateLimiter, WhisperGate
from .router import Router
from .speaker import Decision, Features, SegmentContext, SpeakerClassifier, debug_record
from .spec import defaults, load_spec
from .targets import ChatTarget, CodeTarget, NoteTarget, TargetSet

log = logging.getLogger("voice-claude")
PROTOCOL_VERSION = 1
CLIENT_DIR = Path(__file__).resolve().parents[2] / "client" / "web"


@dataclass
class Settings:
    workspace: str = "~"
    ws_port: int = 8787
    http_port: int = 8788
    token: str = field(default_factory=lambda: secrets.token_urlsafe(12))
    note_path: str = "~/voice-claude/inbox.md"
    ambient_submode: str = "off"


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
            self.ambient.set_submode(msg.get("submode", "off"))
            if msg.get("wipe"):
                self.ambient.wipe()
            await self._send(ws, {"id": "ambient_control", "submode": self.ambient.submode})
        elif kind == "target_switch":
            self.router.sticky_target = msg.get("target")
            await self._send(ws, {"id": "route", "target": self.router.sticky_target,
                                  "reason": "explicit_prefix", "confidence": 1.0})
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

        if not self.classifier.may("execute", decision):
            await self._send(ws, {"id": "route", "target": None, "reason": "role_gate",
                                  "role": decision.role, "confidence": round(decision.confidence, 3),
                                  "label": decision.label_ru})
            return

        if self.router.is_misroute_recovery(text):
            self.router.sticky_target = None
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
        summary = formatter.summarize(reply.text)

        self.machine.to(state.SPEAKING)
        self.machine.open_window()
        await self._broadcast({"id": "voice_summary", "text": summary.text,
                               "is_question": summary.is_question, "target": route.target,
                               "stubbed": reply.stubbed, "full_output": reply.full_output})
        await self._broadcast_state()

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

    # -- approvals -------------------------------------------------------
    async def _ask_permission(self, tool_name: str, input_data: dict[str, Any]) -> bool:
        raw = f"{tool_name} {json.dumps(input_data, ensure_ascii=False)[:200]}"
        request_id = secrets.token_hex(6)
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._broadcast({"id": "permission_request", "request_id": request_id,
                               "spoken": formatter.approval_to_speech(raw), "raw": raw,
                               "earcon": "needs_approval"})
        try:
            return await asyncio.wait_for(future, timeout=60)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(request_id, None)

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
        future.set_result(msg.get("decision", "reject").startswith("approve"))

    async def _on_interrupt(self, ws: Any, msg: dict[str, Any]) -> None:
        if msg.get("scope") == "work":
            await self.targets.code.interrupt()
            self.machine.to(state.LISTENING)
        else:
            self.machine.to(state.LISTENING)
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


def serve_client_files(port: int, directory: Path = CLIENT_DIR) -> ThreadingHTTPServer:
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


async def run(settings: Settings) -> None:
    from websockets.asyncio.server import serve

    daemon = Daemon(settings)
    serve_client_files(settings.http_port)
    url = f"http://<этот-хост>:{settings.http_port}/?port={settings.ws_port}&token={settings.token}"
    print(f"voice-claude-daemon\n  workspace : {Path(settings.workspace).expanduser()}\n"
          f"  websocket : ws://0.0.0.0:{settings.ws_port}\n  client    : {url}\n"
          f"  token     : {settings.token}\n"
          f"  code      : {'ready' if daemon.targets.code.available else 'stub (нет SDK/ключа)'}\n"
          f"  chat      : {'ready' if daemon.targets.chat.available else 'stub (нет SDK/ключа)'}")
    async with serve(daemon.handler, "0.0.0.0", settings.ws_port):
        await asyncio.Future()
