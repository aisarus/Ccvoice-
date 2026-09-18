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
from urllib.parse import parse_qs, urlsplit

from websockets.datastructures import Headers
from websockets.http11 import Response

from . import (checkpoints, formatter, glossary, i18n, learning, lexicon, memory,
               policy, state, stress, tasks, telegram, watcher)
from .ambient import AmbientBuffer, RateLimiter, WhisperGate
from .auth import (SetupError, SetupTokenFlow, apply_token, credential_kind,
                   credential_problem, forget_cli_probe, github_ready, persist_token,
                   probe_cli, token_problem)
from .router import Route, Router
from .speaker import Decision, Features, SegmentContext, SpeakerClassifier, debug_record
from .spec import defaults, load_spec
from .targets import ChatTarget, CodeTarget, NoteTarget, TargetSet

log = logging.getLogger("voice-claude")
PROTOCOL_VERSION = 1
# Демон рассчитан на один-два телефона одного человека. Потолок нужен не от
# нагрузки, а от того, что адрес перестаёт быть неизвестным: без токена никто
# ничего не сделает, но сокеты открывать можно бесконечно, и они стоят памяти.
MAX_SOCKETS = 32
# Соединение, которое молчит вместо hello, — не телефон.
HELLO_TIMEOUT_S = 10.0
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
    # auto — спрашивать модель, когда словарь не уверен; off — только словарь.
    router_model: str = "auto"
    # Язык ответа по умолчанию. Телефон и сам говорящий его перебивают.
    language: str = ""
    # Долгая работа не должна молчать: первое «работаю» и повторы.
    first_ack_s: float = 0.0
    progress_gap_s: float = 0.0

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
            router_model=os.environ.get("ROUTER_MODEL", "auto"),
            language=os.environ.get("VOICE_LANG", ""),
        )


class Daemon:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.machine = state.Machine(on_change=self._on_state_change)
        self.classifier = SpeakerClassifier()
        self.router = Router(config={**defaults("targets"),
                                     "intent_model": settings.router_model})
        self.ambient = AmbientBuffer(submode=settings.ambient_submode)
        self.whisper = WhisperGate()
        self.proactive_limit = RateLimiter()
        self.targets = TargetSet(
            code=CodeTarget(settings.workspace, permission_hook=self._ask_permission),
            chat=ChatTarget(),
            note=NoteTarget(settings.note_path),
        )
        self.clients: set[Any] = set()
        # Все открытые сокеты, включая ещё не назвавшие токен.
        self.sockets: set[Any] = set()
        self.telemetry: list[dict[str, Any]] = []
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._setup: SetupTokenFlow | None = None
        self._pending_tools: dict[str, str] = {}
        self._glossary: str | None = None
        self.memory = memory.Memory(settings.workspace)
        self.journal = checkpoints.Journal(settings.workspace)
        self.examples = learning.Examples(settings.workspace)
        self._last_utterance: tuple[str, str] | None = None   # текст и куда ушло
        self._last_output: dict[str, str] = {}                # что ответила каждая цель
        self._device_of: dict[Any, str] = {}                 # какое соединение чьё
        self.watcher = watcher.Watcher(settings.workspace)
        # Очередь фоновых задач: сказал и забыл.
        self.queue = tasks.TaskQueue(settings.workspace)
        self._pending_news: list[str] = []
        # Язык ответа, закреплённый голосом. None — отвечать на языке вопроса.
        self._reply_language: str | None = None
        background = load_spec()["config_defaults"]["background"]
        self._first_ack_s = settings.first_ack_s or background["first_ack_after_ms"] / 1000
        self._progress_gap_s = settings.progress_gap_s or background["min_interval_between_events_s"]
        self.preapproved: set[str] = set()
        # Реплики обрабатываются параллельно, а Claude-сессия, рабочая копия и
        # состояние машины — одни на всех. Счётчик говорит, сколько реплик ещё
        # в работе; замок держит целиком «запомнить HEAD — поработать —
        # закоммитить», иначе точка отката указывает не туда, куда обещали.
        self._busy = 0
        self._code_turn = asyncio.Lock()
        self._undo_wait_s = 15.0
        self._last_progress_at = 0.0
        # О том, что точку отката записать не вышло, говорим один раз: это
        # свойство рабочего каталога, а не новость каждой реплики.
        self._warned_no_undo = False

    # -- transport -------------------------------------------------------
    async def handler(self, websocket: Any) -> None:
        """Читать не переставая.

        Реплику нельзя обрабатывать прямо в цикле чтения: пока Claude работает
        над прошлой, сокет не читается — ни новая реплика, ни «стоп» не
        доходят, и телефон выглядит оглохшим. Поэтому всё, кроме приветствия,
        уходит в отдельную задачу, а цикл возвращается к чтению.
        """
        self._loop = asyncio.get_running_loop()
        working: set[asyncio.Task[Any]] = set()
        if len(self.sockets) >= MAX_SOCKETS:
            log.warning("too many open connections (%d), refusing a new one", len(self.sockets))
            await self._close_quietly(websocket, 1013, "too many connections")
            return
        self.sockets.add(websocket)
        deadline = asyncio.create_task(self._hello_deadline(websocket))
        try:
            async for raw in websocket:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await self._send(websocket, {"id": "error", "code": "bad_json",
                                                 "message": "not JSON", "recoverable": True})
                    continue
                # Приветствие решает, пускать ли вообще, — оно по порядку.
                if message.get("id") == "hello":
                    await self._dispatch(websocket, message)
                    continue
                if websocket not in self.clients:
                    await self._send(websocket, {"id": "error", "code": "unauthorized",
                                                 "message": "hello first", "recoverable": False})
                    continue
                task = asyncio.create_task(self._guarded(websocket, message))
                working.add(task)
                task.add_done_callback(working.discard)
        finally:
            # Начатую работу не обрываем: телефон переподключается сам, а
            # брошенная посреди дела правка — худшее, что можно сделать.
            deadline.cancel()
            self.sockets.discard(websocket)
            self.clients.discard(websocket)
            self._device_of.pop(websocket, None)

    def _pin_language(self, wanted: Any) -> None:
        """Закрепить язык ответа или снять закрепление.

        Пустая строка и `auto` — это «как спросили», а не «неизвестный язык»:
        снять закрепление голосом должно быть так же просто, как поставить.
        """
        if wanted in (None, "", "auto"):
            self._reply_language = None
            return
        self._reply_language = i18n.normalize(wanted) or self._reply_language

    async def _hello_deadline(self, ws: Any) -> None:
        """Сокет, не назвавший токен, живёт десять секунд.

        Иначе открытые и молчащие соединения копятся до потолка и занимают
        место, которое нужно настоящему телефону при переподключении.
        """
        try:
            await asyncio.sleep(HELLO_TIMEOUT_S)
        except asyncio.CancelledError:
            return
        if ws not in self.clients:
            log.info("closing a connection that never said hello")
            await self._close_quietly(ws, 1008, "hello first")

    async def _guarded(self, ws: Any, msg: dict[str, Any]) -> None:
        """Ни одна поломка не имеет права стать тишиной.

        Пока реплика обрабатывалась в цикле чтения, любое исключение рвало
        связь — телефон это видел и переподключался. Теперь каждая реплика
        живёт в своей задаче, и необработанное исключение не видно вообще
        никак: связь цела, а ответа нет и не будет. Поэтому здесь ловится всё,
        что не поймали ниже, и превращается в короткую фразу в ухо.
        """
        try:
            await self._dispatch(ws, msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                  # noqa: BLE001 - причин много
            log.exception("could not handle %s", msg.get("id"))
            self.machine.to(state.SPEAKING)
            self.machine.open_window()
            await self._say(i18n.t("shell.failure", reason=formatter.reason_for_voice(exc)))
            await self._send(ws, {"id": "error", "code": "internal",
                                  "message": str(exc)[:400], "recoverable": True})
            await self._broadcast_state()

    async def _dispatch(self, ws: Any, msg: dict[str, Any]) -> None:
        kind = msg.get("id")
        if kind == "hello":
            if msg.get("token") != self.settings.token:
                await self._send(ws, {"id": "error", "code": "unauthorized",
                                      "message": "bad token", "recoverable": False})
                await ws.close()
                return
            # Телефон переподключается сам: при смене сети, при возврате из
            # сна, при перезапуске службы. Прежнее соединение того же
            # устройства может ещё не отвалиться по таймауту, и тогда каждый
            # ответ уходит дважды и трижды — человек слышит его хором.
            # Язык телефона — это то, на чём человек собирается говорить.
            # Услышанное всё равно перебивает, если язык не закреплён.
            self._pin_language(msg.get("reply_language"))
            i18n.use(self._reply_language or msg.get("language"))
            device = str(msg.get("device_id") or "")
            if device:
                for previous in [c for c in self.clients
                                 if self._device_of.get(c) == device and c is not ws]:
                    self.clients.discard(previous)
                    self._device_of.pop(previous, None)
                    asyncio.create_task(self._close_quietly(previous))
                self._device_of[ws] = device
            self.clients.add(ws)
            # Пока никто не слушал, новости копились — отдаём их первым делом.
            news, self._pending_news = self._pending_news, []
            for item in news:
                await self._send(ws, self._voice(item, proactive=True))
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
                "permission_mode": policy.mode(),
                "language": i18n.current(),
                "github": github_ready(),
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
        elif kind == "set_language":
            # «Клод, английский» — закрепить язык ответа. Пустое значение
            # снимает закрепление и возвращает «отвечать как спросили».
            self._pin_language(msg.get("reply"))
            i18n.use(self._reply_language or msg.get("language"))
            await self._send(ws, {"id": "language", "reply": self._reply_language or "",
                                  "speaking": i18n.current()})
        elif kind == "ping":
            await self._send(ws, {"id": "ping", "ts": int(time.time() * 1000)})
        else:
            await self._send(ws, {"id": "error", "code": "unknown_message",
                                  "message": str(kind), "recoverable": True})

    # -- the voice loop --------------------------------------------------
    async def _on_segment(self, ws: Any, msg: dict[str, Any]) -> None:
        text = (msg.get("transcript") or "").strip()
        # Отвечаем на языке реплики, а не настройки: человек, перешедший на
        # английский посреди разговора, не должен лезть в настройки телефона.
        # Но если язык ответа закреплён голосом — он и решает: человек просил
        # отвечать на нём, и язык вопроса этой просьбы не отменяет.
        if "reply_language" in msg:
            self._pin_language(msg.get("reply_language"))
        i18n.use(self._reply_language or i18n.detect(text))
        decision = self._classify(msg)
        device = msg.get("device", "phone_mic")

        if decision.role == "self_echo" or not text:
            return

        # Услышанное вокруг — только в буфер, и дальше ни шагу.
        #
        # Телефон помечает так реплики, которых человек оболочке не
        # адресовал. Полагаться здесь на классификатор говорящего нельзя: он
        # ошибается, а цена ошибки — исполненное действие по чужой фразе из
        # соседнего разговора. Кто сказал — вопрос акустики, а кому сказали —
        # вопрос факта, и факт нам присылают.
        if msg.get("ambient"):
            self.ambient.overhear(text, decision.confidence)
            return

        # Вопрос к буферу в буфер не кладём: он ничего не говорит об
        # окружающем мире и только засоряет то, что прочтёт Claude.
        if not AmbientBuffer.is_recall(text):
            self.ambient.add(decision.role, text, decision.confidence)

        if self._pending:
            await self._answer_permission_by_voice(ws, text, decision)
            return

        if not self.classifier.may("execute", decision):
            await self._send(ws, {"id": "route", "target": None, "reason": "role_gate",
                                  "role": decision.role, "confidence": round(decision.confidence, 3),
                                  "label": decision.label})
            return

        if await self._handle_spoken_command(ws, text):
            return

        if self.router.is_misroute_recovery(text):
            await self._reroute(ws, text, decision, device)
            return

        # «Перекинь это в код», «объясни попроще» — это уже выбранная цель,
        # спрашивать про неё словарь и модель незачем.
        handoff = self.router.handoff_target(text)
        if handoff is not None:
            await self._handoff(ws, text, decision, device, handoff)
            return

        route = self.router.route(text, ms_since_last=self.machine.ms_since_window(),
                                  learned=self.examples.suggest(text))
        if self.router.needs_intent_model(route) and self.targets.intent is not None:
            guess = await self.targets.intent.classify(route.text,
                                                       timeout=self.router.intent_timeout)
            if guess:
                route = self.router.apply_intent(route, guess)
        await self._send(ws, {"id": "route", "target": route.target, "reason": route.reason,
                              "confidence": round(route.confidence, 3),
                              "earcon": self.router.earcon_for(route.target),
                              "role": decision.role, "label": decision.label})

        self.machine.to(state.THINKING)
        await self._broadcast_state()

        preamble = self._preamble()
        role_line = self.classifier.role_line(decision, device)
        alternatives = self._alternatives_hint(msg)
        if alternatives:
            role_line += "\n" + alternatives
        if route.target == "chat" and AmbientBuffer.is_recall(text):
            # Вопрос к буферу без буфера — это не повод будить Claude: он
            # честно ответит, что ничего не слышал, потратив круг и секунды.
            if not self.ambient.enabled:
                await self._say(i18n.t("ambient.closed"), "chat")
                await self._finish_turn()
                return
            transcript = self.ambient.transcript()
            if not transcript:
                await self._say(i18n.t("ambient.nothing_heard"), "chat")
                await self._finish_turn()
                return
            role_line += "\n" + i18n.t("ambient.transcript_header") + "\n" + transcript

        await self._speak_turn(ws, route, preamble, role_line)

    async def _speak_turn(self, ws: Any, route: Any, preamble: str, role_line: str,
                          remember: str | None = None) -> None:
        """Реплика целиком: очередь на сессию, точка отката, ответ вслух."""
        self._last_utterance = (remember or route.text, route.target)
        self._busy += 1
        try:
            if route.target == "code":
                # Замок на всю реплику, а не только на запрос к сессии: две
                # реплики подряд запоминали один и тот же HEAD, первая
                # закоммитывала правки обеих, а вторая не находила что
                # коммитить. Откат по такой точке снимал и чужую работу.
                async with self._code_turn:
                    reply = await self._run_turn(ws, route, preamble, role_line)
            else:
                reply = await self._run_turn(ws, route, preamble, role_line)
            if reply is None:
                return
            summary = await self._voice_summary(reply, route.target)
            # Последний вывод цели нужен передаче: «объясни попроще» без него
            # уезжает в чат без единого слова о том, что объяснять.
            self._last_output[route.target] = reply.full_output
            await self._broadcast(self._voice(summary.text, is_question=summary.is_question,
                                              target=route.target, stubbed=reply.stubbed,
                                              full_output=reply.full_output))
            await self._finish_turn()
        finally:
            self._busy -= 1
            if self._busy == 0:
                # Работы больше нет — следующая реплика вправе снова сказать
                # «работаю», даже если прошлая говорила это только что.
                self._last_progress_at = 0.0

    async def _handoff(self, ws: Any, text: str, decision: Decision, device: str,
                       target: str) -> None:
        """Передача разговора между целями вместе с контекстом.

        Спека (`targets.router.handoff`) обещает именно перенос: «объясни
        попроще» без прошлого вывода — это вопрос ни о чём, а «перекинь это
        в код» без него заставляет человека пересказывать себя.
        """
        source = self.router.other_target(target)
        context = self._last_output.get(source, "")
        if not context:
            await self._say(i18n.t("handoff.nothing"), "chat")
            return
        lines = [i18n.t("handoff.header", source=source)]
        if self._last_utterance and self._last_utterance[1] == source:
            lines.append(i18n.t("handoff.asked", text=self._last_utterance[0]))
        lines.append(i18n.t("handoff.answer", text=context))
        route = Route(target, "handoff", 1.0, "\n".join(lines) + "\n\n" + text)

        await self._send(ws, {"id": "route", "target": target, "reason": "handoff",
                              "confidence": 1.0, "from": source,
                              "earcon": self.router.earcon_for(target),
                              "role": decision.role, "label": decision.label})
        self.machine.to(state.THINKING)
        await self._broadcast_state()
        await self._speak_turn(ws, route, self._preamble(),
                               self.classifier.role_line(decision, device), remember=text)

    async def _run_turn(self, ws: Any, route: Any, preamble: str, role_line: str) -> Any:
        """Один заход к цели вместе с точкой отката. None — уже всё сказано."""
        workspace = self.targets.code.workspace
        tracked = route.target == "code" and checkpoints.is_repo(workspace)
        before = checkpoints.head(workspace) if tracked else ""
        try:
            reply = await self._await_with_progress(
                self.targets[route.target].send(route.text, preamble, role_line),
                target=route.target)
        except Exception as exc:                      # noqa: BLE001 - причин много
            # Сессия Claude могла не подняться или упасть посреди работы.
            # Молча уронить связь нельзя: в ухе это тишина, а на телефоне —
            # переподключение без единого слова о том, что случилось.
            log.exception("target %s did not answer", route.target)
            await self._recover_from(ws, route.target, exc)
            return None

        if route.target == "code" and not reply.stubbed:
            await self._checkpoint(workspace, tracked, before, route.text)
        return reply

    async def _checkpoint(self, workspace: Path, tracked: bool, before: str, said: str) -> None:
        """Изменения закрываем коммитом: без этого «откати последнее» не на что опереть."""
        if tracked and before:
            try:
                after = checkpoints.commit_all(workspace, i18n.t("checkpoint.commit", said=said[:60]))
                if after:
                    self.journal.add(before, after, said)
                return
            except (RuntimeError, OSError) as exc:
                log.warning("could not record a checkpoint: %s", exc)
        elif tracked:
            # Репозиторий без единого коммита: возвращаться некуда, но сам
            # коммит сделать надо — со следующей реплики откат заработает.
            try:
                checkpoints.commit_all(workspace, i18n.t("checkpoint.commit", said=said[:60]))
                return
            except (RuntimeError, OSError) as exc:
                log.warning("could not make the first commit: %s", exc)
        # Человек должен знать, что откатывать будет нечем, — но узнать об этом
        # один раз, а не после каждой правки.
        if not self._warned_no_undo:
            self._warned_no_undo = True
            await self._say(i18n.t("undo.no_checkpoint"))

    async def _finish_turn(self) -> None:
        """Закрыть реплику: окно диалога открыто, состояние — честное.

        Пока другая реплика ещё в работе, «говорю» — враньё: телефон погасит
        индикатор работы и решит, что всё закончилось.
        """
        self.machine.open_window()
        self.machine.to(state.SPEAKING if self._busy <= 1 else state.WORKING)
        await self._broadcast_state()

    async def _recover_from(self, ws: Any, target: str, exc: Exception) -> None:
        """Сказать вслух, что не вышло, и вернуться в исходное состояние."""
        spoken = i18n.t("target.failed", target=target, reason=formatter.reason_for_voice(exc))
        # Пересоздаём только ту сессию, которая сломалась. Общий сброс ронял
        # долгую Claude Code-сессию из-за того, что не записался инбокс или
        # икнула разговорная цель, — и работа начиналась с чистого листа.
        failed = self.targets[target]
        if hasattr(failed, "reset"):
            await failed.reset()
        await self._broadcast(self._voice(spoken, target=target, stubbed=True,
                                          full_output=str(exc)))
        await self._send(ws, {"id": "error", "code": "target_failed",
                              "message": str(exc)[:400], "recoverable": True})
        await self._finish_turn()

    async def _await_with_progress(self, coro: Any, target: str = "code") -> Any:
        """Ждать ответ, не молча.

        Спека (`latency_targets.long_task_rule`): если работа затянулась, через
        пару секунд надо сказать об этом, а потом изредка напоминать, что она
        идёт — тишина в наушнике неотличима от поломки.
        """
        task = asyncio.ensure_future(coro)
        said = 0
        while True:
            timeout = self._first_ack_s if said == 0 else self._progress_gap_s
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except asyncio.TimeoutError:
                said += 1
                if said == 1:
                    self.machine.to(state.WORKING)
                    await self._broadcast_state()
                await self._progress(target, i18n.t("shell.working") if said == 1
                                     else i18n.t("shell.still_working"))

    async def _progress(self, target: str, text: str) -> None:
        """«Работаю» — про весь демон, а не про каждую реплику.

        Две реплики подряд начинали работу одновременно и говорили это хором:
        в ухе получалось «работаю работаю». Напоминание одно на всех и не чаще
        того же интервала, которым разрежены остальные фоновые события.
        """
        now = time.monotonic()
        if now - self._last_progress_at < self._progress_gap_s:
            return
        self._last_progress_at = now
        await self._say(text, target=target, progress=True)

    async def watch_loop(self, interval_s: float = 60.0) -> None:
        """Смотрит наружу и заговаривает первым. Остановить — PROACTIVE=off."""
        while True:
            await asyncio.sleep(interval_s)
            try:
                events = await asyncio.to_thread(self.watcher.check)
                for event in events:
                    log.info("proactive: %s", event.text)
                    await self._announce(event.text)
                    if watcher.mode() == "fix" and event.fix_prompt:
                        await self._fix_it(event)
            except Exception as exc:            # наблюдатель не должен ронять демон
                # Починка внутри цикла тоже: упавший «fix» уносил с собой весь
                # цикл, и проактивность молча выключалась до перезапуска.
                log.warning("watcher: %s", exc)
                continue

    async def _announce(self, text: str) -> None:
        """Сказать сейчас или придержать до того, как кто-то подключится."""
        if self.clients:
            await self._broadcast(self._voice(text, proactive=True))
        else:
            self._pending_news.append(text)
            del self._pending_news[:-10]

    async def _fix_it(self, event: Any) -> None:
        """Режим «fix»: не только сказать, но и починить.

        Замок тот же, что у реплик: чинить в обход очереди — значит писать в
        рабочую копию, пока над ней работает сказанное голосом.
        """
        async with self._code_turn:
            reply = await self._await_with_progress(
                self.targets.code.send(event.fix_prompt, self._preamble(), ""))
        summary = await self._voice_summary(reply, "code")
        await self._announce(summary.text)

    def _voice(self, text: str, **extra: Any) -> dict[str, Any]:
        """Голосовая реплика: на экран — чистый текст, в синтез — с ударениями.

        Разметку ударений нельзя показывать человеку и нельзя терять: русский
        синтез без неё ошибается в технических словах, а со знаками на экране
        читать невозможно. Поэтому два поля.
        """
        # Знаки ударения понимает только русский синтез: в английской или
        # китайской реплике «+» — это просто плюс, который прочтут вслух.
        marked = stress.mark(text) if i18n.uses_stress_marks() else text
        payload: dict[str, Any] = {"id": "voice_summary", "text": stress.clean(text),
                                   "is_question": False, "stubbed": False,
                                   "full_output": text, "target": "code"}
        payload.update(extra)
        if stress.is_marked(marked):
            payload["spoken"] = marked
        return payload

    async def _say(self, text: str, target: str = "code", progress: bool = False) -> None:
        """Реплика от самой оболочки.

        `progress=True` — только для «работаю»: этим флагом клиент вправе не
        показывать реплику. «Откатил auth.ts» и «Запомнил» — это ответы, и
        помечать их так означает терять их на клиенте, который флаг уважает.
        """
        await self._broadcast(self._voice(text, target=target, progress=progress))

    @staticmethod
    def _alternatives_hint(msg: dict[str, Any]) -> str:
        """Другие гипотезы распознавателя.

        Переписывать реплику за человека нельзя — это испорченный телефон из
        спеки. Но верный вариант часто стоит вторым, особенно на именах из
        проекта, и пусть Claude выберет по контексту, как он это делает
        с глоссарием.
        """
        raw = msg.get("alternatives") or []
        alts = [a.strip() for a in raw if isinstance(a, str) and a.strip()][:3]
        if not alts:
            return ""
        return i18n.t("asr.alternatives") + " · ".join(alts)

    def _preamble(self) -> str:
        """Служебная приписка: голосовой ввод, имена проекта, память о человеке."""
        additions = load_spec()["command_passthrough"]["allowed_additions"]
        preamble = (additions.get("system_preamble_by_language", {}).get(i18n.current())
                    or additions["system_preamble"])
        # Язык называется прямо: сессия Claude Code живёт долго и помнит, на
        # чём шёл прежний разговор, — без этой строки она продолжает отвечать
        # на нём даже после того, как человек перешёл на другой.
        parts = ["\n".join(preamble.splitlines()), i18n.t("prompt.answer_language")]
        for extra in (self._glossary_hint(), self.memory.hint()):
            if extra:
                parts.append(extra)
        return "\n".join(parts)

    def _glossary_hint(self) -> str:
        if self._glossary is None:
            self._glossary = glossary.for_workspace(self.targets.code.workspace)
        return self._glossary

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
        def measured(name: str) -> float | None:
            """Не прислали — значит не мерили.

            Прежде сюда подставлялись «разумные» числа, и молчание телефона
            превращалось в измеренный плохой результат: хозяин выходил
            соседом, и команда молча не исполнялась. Отсутствие признака —
            это отсутствие, а не плохое значение.
            """
            value = raw.get(name)
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        features = Features(
            level_rel_db=measured("level_rel_db"),
            snr_db=measured("snr_db"),
            drr_db=measured("drr_db"),
            c50_db=measured("c50_db"),
            hf_ratio_db=measured("hf_ratio_db"),
            lf_proximity_db=measured("lf_proximity_db"),
            voiceprint_similarity=measured("voiceprint_similarity"),
        )
        ctx = SegmentContext(
            device=msg.get("device", "phone_mic"),
            # Полосу слышит только клиент: канал гарнитуры узкополосный,
            # и на нём часть признаков просто не измерить.
            narrowband=bool(msg.get("narrowband", False)),
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
                                  "message": "an access token is required", "recoverable": False})
            return
        try:
            # Токен уже на руках — вставили его прямо в приложении.
            if msg["id"] == "auth_set":
                token = str(msg.get("token", "")).strip()
                problem = token_problem(token)
                if problem:
                    raise SetupError(i18n.t("auth.token_mismatch", problem=problem))
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
                raise SetupError(i18n.t("auth.flow_not_started"))
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
        # Безопасное делаем молча: спрашивать про каждый git status — издевательство.
        if policy.decide_in_mode(tool_name, input_data) == "allow":
            log.info("allowed by policy: %s", tool_name)
            return True
        if tool_name in self.preapproved and policy.may_remember(tool_name, input_data):
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

    async def _handle_spoken_command(self, ws: Any, text: str) -> bool:
        """Команды самой оболочки: откат, история, память.

        Они выполняются здесь, а не уходят в Claude: «откати последнее» должно
        работать мгновенно и одинаково, даже когда сессия занята.
        """
        if await self._handle_queue_command(ws, text):
            return True
        wanted = telegram.share_request(text)
        if wanted is not None:
            await self._share(wanted)
            return True
        if lexicon.starts_with(text, lexicon.every("second_ear_off")):
            await self._second_ear(False)
            return True
        if lexicon.starts_with(text, lexicon.every("second_ear_on")):
            await self._second_ear(True)
            return True
        if checkpoints.matches(text, checkpoints.UNDO_PHRASES):
            await self._undo()
            return True
        if checkpoints.matches(text, checkpoints.HISTORY_PHRASES):
            await self._recent_changes()
            return True

        fact = memory.remember_intent(text)
        if fact is not None:
            saved = self.memory.remember(fact)
            await self._say(i18n.t("memory.saved") if saved
                            else i18n.t("memory.nothing_to_save"), "chat")
            return True

        forgotten = memory.forget_intent(text)
        if forgotten:
            count = self.memory.forget(forgotten)
            await self._say(i18n.t("memory.forgot") if count
                            else i18n.t("memory.not_remembered"), "chat")
            return True

        if memory.recall_intent(text):
            facts = self.memory.facts()
            spoken = (i18n.t("memory.recall", facts="; ".join(facts[-5:]))
                      if facts else i18n.t("memory.empty"))
            await self._say(spoken, "chat")
            return True
        return False

    async def _second_ear(self, wanted: bool) -> None:
        """«Клод, второе ухо» — слушать, что вокруг, и пересказывать по просьбе.

        Чужая речь в буфер по умолчанию не попадает: это отдельный опт-ин в
        спеке, и открыть ухо, не открыв его, значило бы копить пустоту.
        Поэтому команда включает и то, и другое разом, а вслух напоминает
        про согласие собеседников — один раз, при открытии.
        """
        if wanted == self.ambient.enabled:
            await self._say(i18n.t("ambient.already_on" if wanted else "ambient.already_off"),
                            "chat")
            return
        self.ambient.set_submode("passive" if wanted else "off")
        self.ambient.set_bystander_transcript(wanted)
        await self._broadcast({"id": "ambient_control", "submode": self.ambient.submode,
                               "bystander_transcript": self.ambient.bystander_transcript,
                               "lines": len(self.ambient.lines())})
        await self._say(i18n.t("ambient.on" if wanted else "ambient.off"), "chat")

    async def _reroute(self, ws: Any, text: str, decision: Decision, device: str) -> None:
        """«Не туда»: переслать прошлую реплику в другую цель и запомнить урок."""
        self.router.force(None)
        if self._last_utterance is None:
            await self._say(i18n.t("reroute.nothing"), "chat")
            return
        said, was = self._last_utterance
        # «не туда, в чат» — цель названа прямо; иначе берём противоположную.
        named = self.router.route(text.lower(), ms_since_last=0)
        target = named.target if named.reason == "explicit_prefix" else self.router.other_target(was)
        self.examples.remember(said, target)
        log.info("correction: %r -> %s (was %s)", said[:40], target, was)

        await self._send(ws, {"id": "route", "target": target, "reason": "corrected",
                              "confidence": 1.0, "learned_from": was})
        self.machine.to(state.THINKING)
        await self._broadcast_state()

        # Поправка — такая же реплика: и точку отката ей надо, и от поломки её
        # надо прикрыть, и замок на сессию действует тот же.
        await self._speak_turn(ws, Route(target, "corrected", 1.0, said), self._preamble(),
                               self.classifier.role_line(decision, device))

    # -- мост в telegram --------------------------------------------------
    async def _share(self, wanted: str) -> None:
        """«Скинь мне в телегу» — файл уезжает в один заранее заданный чат.

        Ни адресата, ни путь голосом задать нельзя: рядом могут говорить
        другие, а мост, слушающийся любого, — это способ вынести файлы из
        машины чужими руками.
        """
        bridge = telegram.Bridge()
        if not bridge.ready:
            await self._say(i18n.t("telegram.not_configured",
                                   command="bash scripts/setup-telegram.sh"))
            return

        workspace = Path(self.targets.code.workspace)
        files = self._files_to_share(wanted, workspace)

        if not files:
            # Файла не нашлось — но сказанному обычно предшествовал ответ,
            # и человек чаще всего хочет именно его.
            text = self._last_output.get("code") or self._last_output.get("chat") or ""
            if not text:
                await self._say(i18n.t("telegram.unclear"))
                return
            problem = await asyncio.to_thread(bridge.send_text, text)
            await self._say(i18n.t("telegram.sent_one") if problem is None
                            else i18n.t("telegram.not_sent", problem=problem))
            return

        sent, refused = [], []
        for path in files:
            reason = telegram.refuse_reason(path, workspace)
            if reason:
                refused.append(f"{path.name}: {reason}")
                continue
            problem = await asyncio.to_thread(bridge.send_document, path, path.name)
            (sent if problem is None else refused).append(
                path.name if problem is None else f"{path.name}: {problem}")

        spoken = []
        if sent:
            spoken.append(i18n.t("telegram.sent_list", names=i18n.join(sent[:3]))
                          + (i18n.t("telegram.and_more", rest=len(sent) - 3)
                             if len(sent) > 3 else ""))
        if refused:
            spoken.append(i18n.t("telegram.refused_list", reasons="; ".join(refused[:2])))
        await self._say(". ".join(spoken) or i18n.t("telegram.nothing"))

    # «Скинь мне ЕГО в телегу» — это не имя файла, а «то, что мы сейчас делали».
    PRONOUNS = {"его", "это", "этот", "её", "ее", "их", "то", "тот", "файл", "их же",
                "it", "this", "that", "the file", "them", "those",
                "lo", "eso", "esto", "el archivo", "ese",
                "它", "这个", "那个", "文件"}

    def _files_to_share(self, wanted: str, workspace: Path) -> list[Path]:
        """Что именно человек просит отправить.

        Порядок: названное им имя, потом то, что Claude только что менял,
        потом просто самое свежее в проекте. Несуществующее не предлагаем
        никогда: «такого файла нет» — плохой ответ на «скинь мне его».
        """
        stem = wanted.lower().strip(" .,")
        if stem in self.PRONOUNS:
            stem = ""

        candidates = self._project_files(workspace)
        if stem:
            exact = [p for p in candidates
                     if stem in p.name.lower() or stem in p.stem.lower()]
            if exact:
                return exact[:5]
            # Он говорит «конфиг», а файл называется config.json.
            similar = telegram.best_match(stem, [p.name for p in candidates])
            if similar:
                return [p for p in candidates if p.name == similar][:1]

        changed = self._recently_changed(workspace)
        if changed:
            return changed
        # Точки отката может не быть вовсе — правка ещё не закрыта коммитом.
        # Тогда «его» — это самое свежее, но не тест: человек просил игру, а
        # тест к ней записывается последним и уезжал вместо неё.
        newest = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
        main = [p for p in newest if not self._is_auxiliary(p, workspace)]
        return (main or newest)[:3]

    def _recently_changed(self, workspace: Path) -> list[Path]:
        """Файлы последней точки отката — только те, что ещё существуют."""
        point = self.journal.last()
        if point is None or not checkpoints.is_repo(workspace):
            return []
        try:
            names = checkpoints.changed_files(workspace, point.before, point.after)
        except (RuntimeError, OSError):
            return []
        # В diff попадают и удалённые файлы: отправлять их нечем.
        alive = [p for p in (workspace / name for name in names) if p.is_file()]
        main = [p for p in alive if not self._is_auxiliary(p, workspace)]
        return (main or alive)[:5]

    @staticmethod
    def _is_auxiliary(path: Path, workspace: Path) -> bool:
        """Тесты и сборочный мусор — не то, что человек просит «скинуть»."""
        parts = [p.lower() for p in path.relative_to(workspace).parts]
        if any(p in ("test", "tests", "spec", "__tests__", "build", "dist") for p in parts):
            return True
        name = path.stem.lower()
        return (name.startswith(("test_", "test-", "spec_"))
                or name.endswith(("_test", "-test", ".test", "_spec", ".spec")))

    @staticmethod
    def _project_files(workspace: Path) -> list[Path]:
        found: list[Path] = []
        for path in workspace.rglob("*"):
            if not path.is_file():
                continue
            parts = path.relative_to(workspace).parts
            if any(p.startswith(".") or p in ("node_modules", "__pycache__")
                   for p in parts):
                continue
            found.append(path)
            if len(found) >= 500:          # большой проект целиком не нужен
                break
        return found

    # -- очередь фоновых задач -------------------------------------------
    async def _handle_queue_command(self, ws: Any, text: str) -> bool:
        """«В фоне почини тесты», «чем занят», «что готово», «отмени задачу…».

        Фоновой задачу делает решение человека не ждать ответа, а не её
        длительность, — поэтому спрашиваем его, а не угадываем сами.
        """
        wanted = tasks.background_request(text)
        if wanted:
            if not checkpoints.is_repo(self.targets.code.workspace):
                await self._say(i18n.t("tasks.repo_only"))
                return True
            task = self.queue.add(wanted)
            await self._say(i18n.t("tasks.accepted", title=task.title))
            return True

        if tasks.is_status_question(text):
            await self._say(self.queue.spoken_status())
            return True

        if tasks.is_ready_question(text):
            ready = self.queue.by_state(tasks.DONE)
            if not ready:
                await self._say(i18n.t("tasks.none_ready"))
            else:
                last = ready[-1]
                await self._say(i18n.t("tasks.ready_report", count=len(ready),
                                       title=last.title, summary=last.summary))
            return True

        words = tasks.cancel_request(text)
        if words:
            task = self.queue.find(words)
            if task is None:
                await self._say(i18n.t("tasks.not_found"))
            else:
                self.queue.cancel(task)
                await self._say(i18n.t("tasks.cancelled", title=task.title))
            return True
        return False

    async def queue_loop(self, interval_s: float = 3.0) -> None:
        """Запускает задачи и рассказывает о готовых, когда в ухе тихо."""
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self._settle()
                await self._start_next_task()
                await self._deliver_finished()
            except Exception as exc:            # очередь не должна ронять демон
                log.warning("queue: %s", exc)

    async def _settle(self) -> None:
        """Окно диалога закрылось и работы нет — значит снова тишина.

        Телефон не сообщает, что дочитал ответ, поэтому демон оставался в
        «говорю» навсегда: индикатор врал, а доклад фоновой задачи, который
        ждёт тишины, не наступал никогда.
        """
        if self._busy or self._pending:
            return
        if self.machine.state in (state.SPEAKING, state.LISTENING, state.THINKING) \
                and not self.machine.window_open():
            self.machine.to(state.IDLE)
            await self._broadcast_state()

    async def _start_next_task(self) -> None:
        task = self.queue.next_to_start()
        if task is None or not checkpoints.is_repo(self.targets.code.workspace):
            return
        try:
            self.queue.start(task)
        except (RuntimeError, OSError) as exc:
            self.queue.finish(task, i18n.t("tasks.did_not_start", reason=exc), stuck=True)
            return
        log.info("task %s: %s", task.id, task.title)
        asyncio.create_task(self._run_task(task))

    async def _run_task(self, task: tasks.Task) -> None:
        """Отдельная сессия в отдельной копии: соседям она не мешает."""
        target = CodeTarget(task.worktree)
        try:
            reply = await target.send(task.text, self._preamble(), "")
            summary = await self._voice_summary(reply, "code")
            await asyncio.to_thread(checkpoints.commit_all, task.worktree,
                                    i18n.t("tasks.commit", said=task.text[:60]))
            self.queue.finish(task, summary.text, stuck=reply.stubbed)
        except Exception as exc:                # noqa: BLE001 - причин много
            log.exception("task %s fell over", task.id)
            self.queue.finish(task, formatter.reason_for_voice(exc), stuck=True)
        finally:
            await target.reset()

    async def _deliver_finished(self) -> None:
        """Готовое не выкрикивается поверх разговора: ждём тишины."""
        ready = self.queue.undeliverable()
        if not ready or self._pending:
            return
        if self.machine.state not in (state.IDLE, state.LISTENING):
            return
        for task in ready:
            # Пересказ обычно уже начинается с «готово» — своё слово добавляем
            # только к сорвавшейся задаче, иначе в ухе звучит заедание.
            prefix = "" if task.state == tasks.DONE else i18n.t("tasks.stalled_prefix")
            await self._announce(f"{prefix}{task.title}: {task.summary}")
        self.queue.mark_delivered(ready)

    async def _undo(self) -> None:
        workspace = self.targets.code.workspace
        point = self.journal.last()
        if point is None or not checkpoints.is_repo(workspace):
            await self._say(i18n.t("undo.nothing"))
            return
        # `git reset --hard` посреди работы — это откат под руками у Claude:
        # часть правок уже на диске, часть ещё нет, и вернётся мешанина.
        # Работу останавливаем и ждём, пока реплика свернётся сама.
        if self._code_turn.locked():
            await self.targets.code.interrupt()
        try:
            await asyncio.wait_for(self._code_turn.acquire(), timeout=self._undo_wait_s)
        except asyncio.TimeoutError:
            await self._say(i18n.t("undo.busy"))
            return
        try:
            try:
                changed = checkpoints.summary(workspace, point.before, point.after)
                checkpoints.reset_to(workspace, point.before)
            except (RuntimeError, OSError) as exc:
                await self._say(i18n.t("undo.failed", reason=formatter.reason_for_voice(exc)))
                return
            self.journal.pop()
            await self.targets.code.reset()  # сессия должна увидеть новое состояние
            await self._say(i18n.t("undo.done", changed=changed, title=point.title))
        finally:
            self._code_turn.release()

    async def _recent_changes(self) -> None:
        points = self.journal.recent(3)
        if not points:
            await self._say(i18n.t("history.empty"))
            return
        await self._say(i18n.t("history.last", items="; ".join(p.title for p in points)))

    async def _answer_permission_by_voice(self, ws: Any, text: str, decision: Decision) -> None:
        """Пока висит запрос разрешения, реплика — это ответ на него, а не команда."""
        action = formatter.parse_approval(text)
        if action is None:
            await self._send(ws, {"id": "route", "target": None, "reason": "awaiting_permission",
                                  "role": decision.role, "label": decision.label})
            return
        if action == "speak_details":
            await self._broadcast(self._voice(self._pending_detail(), is_question=True,
                                              target="approval"))
            return
        if not self.classifier.may("approve", decision):
            await self._send(ws, {"id": "route", "target": None, "reason": "approval_role_gate",
                                  "role": decision.role, "confidence": round(decision.confidence, 3),
                                  "label": decision.label})
            return

        request_id = next(iter(self._pending))
        self._resolve_permission({"request_id": request_id, "decision": action,
                                  "role": decision.role, "confidence": decision.confidence})
        approved = action.startswith("approve")
        if action == "approve_and_remember_rule":
            raw = self._pending_tools.get(request_id, "")
            if policy.may_remember(raw.split(" ", 1)[0], {"command": raw}):
                self.preapproved.add(raw)
            else:
                await self._broadcast(self._voice(
                    i18n.t("shell.allowed_once"), full_output=raw))
        await self._broadcast({"id": "permission_result", "request_id": request_id,
                               "approved": approved, "remembered": action.endswith("rule"),
                               "earcon": "accepted" if approved else "error"})

    async def _accept_token(self, token: str) -> None:
        """Применить токен немедленно и сохранить, чтобы пережил перезапуск."""
        apply_token(token)
        forget_cli_probe()
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
        return self._pending_tools.get(request_id, i18n.t("shell.nothing_to_clarify"))

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
            await self._broadcast(self._voice(i18n.t("shell.stopped"), full_output="interrupt"))
        self.machine.to(state.LISTENING)
        self.machine.open_window()
        await self._broadcast_state()

    # -- plumbing --------------------------------------------------------
    def _on_state_change(self, previous: str, current: str) -> None:
        log.info("state %s -> %s", previous, current)

    async def _broadcast_state(self) -> None:
        await self._broadcast({"id": "state", "state": self.machine.state,
                               "window_open": self.machine.window_open()})

    async def _close_quietly(self, ws: Any, code: int = 1000,
                             reason: str = "replaced by a newer connection") -> None:
        """Закрыть соединение, не поднимая шума: прежнее того же телефона,
        молчащее вместо hello или лишнее сверх потолка."""
        try:
            await ws.close(code, reason)
        except Exception:                        # оно могло уже умереть
            pass

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


def make_process_request(directory: Path = CLIENT_DIR,
                         daemon: "Daemon | None" = None) -> Any:
    """HTTP side of the single port: health check, client, everything else 404."""

    async def process_request(connection: Any, request: Any) -> Any:
        path = request.path.split("?")[0]
        if path == "/healthz":
            # Платформе достаточно «ok». Человеку нужно знать, что демон
            # думает про доступ к Claude, — но это не для случайного гостя,
            # поэтому по токену.
            query = parse_qs(urlsplit(request.path).query)
            asked = query.get("token", [""])[0]
            if daemon is not None and asked and asked == daemon.settings.token:
                body = json.dumps({
                    "state": daemon.machine.state,
                    "credential": credential_kind(),
                    "credential_problem": credential_problem(),
                    "code": daemon.targets.code.available,
                    "chat": daemon.targets.chat.available,
                    "permission_mode": policy.mode(),
                    "router_model": daemon.settings.router_model,
                    "workspace": str(Path(daemon.settings.workspace).expanduser()),
                    "github": github_ready(),
                    "telegram": telegram.configured(),
                }, ensure_ascii=False, indent=2) + "\n"
                return http_response(http.HTTPStatus.OK, body.encode("utf-8"),
                                     "application/json; charset=utf-8")
            return http_response(http.HTTPStatus.OK, b"ok\n", "text/plain; charset=utf-8")
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None
        return static_response(path, directory)

    return process_request


async def run(settings: Settings) -> None:
    from websockets.asyncio.server import serve

    i18n.use(settings.language or i18n.default_language())
    await bootstrap_workspace(settings)
    daemon = Daemon(settings)
    process_request = make_process_request(daemon=daemon)

    # Health check стучится раз в секунду: без этого лог состоит из него одного.
    if not log.isEnabledFor(logging.DEBUG):
        logging.getLogger("websockets.server").setLevel(logging.WARNING)
    # SDK предупреждает, что для разрешённых списком инструментов колбэк не
    # вызывается. Так и задумано: список разрешает чтение, колбэк запрещает
    # остальное. В логе это только шум.
    warnings.filterwarnings("ignore", message=".*can_use_tool will not be invoked.*")

    # Ни токена, ни файла входа — это ещё не значит «нет доступа»: CLI бывает
    # авторизован иначе. Спрашиваем его самого, один раз и до приёма реплик.
    if credential_kind() == "none":
        await asyncio.get_running_loop().run_in_executor(None, probe_cli)

    print(f"voice-claude-daemon\n"
          f"  workspace : {Path(settings.workspace).expanduser()}\n"
          f"  listening : {settings.host}:{settings.port} (client and WebSocket on one port)\n"
          f"  language  : {i18n.current()} (the client and the speaker override it)\n"
          f"  token     : {settings.token}\n"
          f"  code      : {'ready' if daemon.targets.code.available else 'stub (no SDK/key)'}\n"
          f"  chat      : {'ready' if daemon.targets.chat.available else 'stub (no SDK/key)'}",
          flush=True)
    # Циклы надо запустить: наблюдатель был написан и покрыт тестами, но его
    # никто никогда не вызывал — проактивность молчала с самого начала.
    background = [asyncio.create_task(daemon.watch_loop()),
                  asyncio.create_task(daemon.queue_loop())]
    try:
        async with serve(daemon.handler, settings.host, settings.port,
                         process_request=process_request):
            await asyncio.Future()
    finally:
        for task in background:
            task.cancel()


def is_own_checkout(workspace: Path) -> bool:
    """Это сам voice-shell, а не чей-то проект."""
    return (workspace / "daemon" / "voice_claude" / "server.py").is_file()


def refuse_own_checkout(workspace: Path) -> None:
    """Свой репозиторий рабочим каталогом быть не может.

    Демон складывает в коммит всё, что найдёт в рабочем каталоге. Наведённый
    на собственную копию, он закоммитил незаконченную работу человека от лица
    «Voice Shell» и тут же откатил её по первому «откати последнее». Молчать
    об этом нельзя: цена ошибки — чужой рабочий день.
    """
    if not is_own_checkout(workspace):
        return
    raise SystemExit(
        f"the working directory {workspace} is voice-shell itself.\n"
        "The daemon commits everything it finds in the working directory, and would\n"
        "commit unfinished work under someone else's name. Point --workspace at a project."
    )


async def bootstrap_workspace(settings: Settings) -> None:
    """On a host with no checkout, clone the repo Claude Code will work in."""
    workspace = Path(settings.workspace).expanduser()
    refuse_own_checkout(workspace.resolve() if workspace.exists() else workspace)
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
