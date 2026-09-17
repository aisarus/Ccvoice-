"""End-to-end over the real WebSocket protocol, with stubbed targets."""
import asyncio
import json
import os
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from voice_claude.server import Daemon, Settings

MASTER = {"level_rel_db": 1.0, "snr_db": 30.0, "drr_db": 10.0, "c50_db": 14.0,
          "hf_ratio_db": 1.0, "lf_proximity_db": 4.0}
BYSTANDER = {"level_rel_db": -14.0, "snr_db": 9.0, "drr_db": -1.0, "c50_db": 2.0,
             "hf_ratio_db": -7.0, "lf_proximity_db": -2.0}


async def _session(segments, note_path):
    settings = Settings(workspace=".", port=0, token="test-token", note_path=str(note_path))
    daemon = Daemon(settings)
    received = []
    async with serve(daemon.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"id": "hello", "v": 1, "token": "test-token"}))
            received.append(json.loads(await ws.recv()))
            for segment in segments:
                await ws.send(json.dumps(segment))
                try:
                    while True:
                        received.append(json.loads(
                            await asyncio.wait_for(ws.recv(), timeout=1.5)))
                except asyncio.TimeoutError:
                    pass
    return daemon, received


def run(segments, note_path):
    return asyncio.run(_session(segments, note_path))


def segment(text, features, **over):
    payload = {"id": "speech_segment", "segment_id": "s1", "transcript": text,
               "device": "phone_mic", "duration_ms": 1400, "voiced_frames": 45,
               "features": features}
    payload.update(over)
    return payload


def kinds(received, kind):
    return [m for m in received if m.get("id") == kind]


def test_handshake_reports_target_availability(tmp_path):
    _, received = run([], tmp_path / "inbox.md")
    welcome = kinds(received, "welcome")[0]
    assert welcome["state"] == "IDLE"
    assert set(welcome["targets"]) == {"code", "chat", "note"}


def test_master_utterance_is_routed_and_answered(tmp_path):
    _, received = run([segment("запиши идею про второе ухо", MASTER)], tmp_path / "inbox.md")
    route = kinds(received, "route")[0]
    assert route["target"] == "note"
    assert route["role"] == "master"
    summary = kinds(received, "voice_summary")[0]
    assert summary["text"] == "Записал."
    assert (tmp_path / "inbox.md").read_text(encoding="utf-8").strip().endswith("второе ухо")


def test_bystander_speech_is_never_executed(tmp_path):
    _, received = run([segment("запиши что я сказал", BYSTANDER)], tmp_path / "inbox.md")
    route = kinds(received, "route")[0]
    assert route["target"] is None
    assert route["reason"] == "role_gate"
    assert route["label"] == "говорит собеседник"
    assert not kinds(received, "voice_summary")
    assert not (tmp_path / "inbox.md").exists()


def test_self_echo_is_dropped_silently(tmp_path):
    _, received = run([segment("записал", MASTER, echo_correlation=0.95)], tmp_path / "inbox.md")
    assert not kinds(received, "route")
    assert not kinds(received, "voice_summary")


def test_code_target_reports_stub_instead_of_pretending(tmp_path):
    daemon, received = run([segment("почини auth.ts и запусти тесты", MASTER)],
                           tmp_path / "inbox.md")
    route = kinds(received, "route")[0]
    assert route["target"] == "code"
    summary = kinds(received, "voice_summary")[0]
    assert summary["stubbed"] is True
    assert "недоступен" in summary["text"]


def test_bad_token_is_rejected(tmp_path):
    async def attempt():
        settings = Settings(workspace=".", token="right", note_path=str(tmp_path / "i.md"))
        daemon = Daemon(settings)
        async with serve(daemon.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"id": "hello", "v": 1, "token": "wrong"}))
                return json.loads(await ws.recv())

    error = asyncio.run(attempt())
    assert error["id"] == "error" and error["code"] == "unauthorized"


def test_telemetry_keeps_features_but_no_audio(tmp_path):
    daemon, _ = run([segment("почини auth.ts", MASTER)], tmp_path / "inbox.md")
    record = daemon.telemetry[0]
    assert record["role"] == "master"
    assert "level_rel_db" in record and "p_master" in record
    assert not any("audio" in key or "pcm" in key for key in record)


def test_headset_channel_switches_to_the_narrowband_profile(tmp_path):
    """Про узкую полосу знает только телефон — и обязан о ней сказать."""
    daemon, _ = run(
        [segment("посмотри логи", MASTER, device="sony_mic", narrowband=True)],
        tmp_path / "inbox.md",
    )
    assert daemon.telemetry[0]["profile"] == "narrowband"


def test_phone_mic_keeps_the_full_band_profile(tmp_path):
    """Без флага полосы профиль остаётся обычным, даже на гарнитуре."""
    daemon, _ = run(
        [segment("посмотри логи", MASTER, device="sony_mic")],
        tmp_path / "inbox.md",
    )
    assert daemon.telemetry[0]["profile"] != "narrowband"


def test_ambient_control_switches_and_wipes(tmp_path):
    async def flow():
        settings = Settings(workspace=".", token="t", note_path=str(tmp_path / "i.md"))
        daemon = Daemon(settings)
        async with serve(daemon.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"id": "hello", "v": 1, "token": "t"}))
                await ws.recv()
                await ws.send(json.dumps({"id": "ambient_control", "submode": "passive"}))
                reply = json.loads(await ws.recv())
                daemon.ambient.add("master", "секрет")
                await ws.send(json.dumps({"id": "ambient_control", "submode": "off", "wipe": True}))
                await ws.recv()
                return reply, daemon

    reply, daemon = asyncio.run(flow())
    assert reply["submode"] == "passive"
    assert daemon.ambient.submode == "off"
    assert daemon.ambient.lines() == []


def test_same_port_serves_the_client_and_health_check(tmp_path):
    """One port for page and socket: hosting platforms expose exactly one."""
    import subprocess

    from voice_claude.server import make_process_request

    async def flow():
        settings = Settings(workspace=".", port=0, token="t", note_path=str(tmp_path / "i.md"))
        daemon = Daemon(settings)
        async with serve(daemon.handler, "127.0.0.1", 0,
                         process_request=make_process_request()) as server:
            port = server.sockets[0].getsockname()[1]

            def fetch(path):
                return subprocess.run(
                    ["curl", "-si", "--max-time", "5", f"http://127.0.0.1:{port}{path}"],
                    capture_output=True, text=True).stdout

            health = await asyncio.to_thread(fetch, "/healthz")
            page = await asyncio.to_thread(fetch, "/")
            escape = await asyncio.to_thread(fetch, "/../../etc/passwd")

            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"id": "hello", "v": 1, "token": "t"}))
                welcome = json.loads(await ws.recv())
            return health, page, escape, welcome

    health, page, escape, welcome = asyncio.run(flow())
    assert "200 OK" in health.splitlines()[0] and health.strip().endswith("ok")
    assert "200 OK" in page.splitlines()[0] and "text/html" in page.lower()
    assert "Voice Shell" in page
    assert "404" in escape.splitlines()[0]
    assert welcome["id"] == "welcome"

    # Дублированный Content-Length (0 и настоящая длина) — это то, из-за чего
    # прокси Render отдавал 502 при живом контейнере.
    for name, raw in (("health", health), ("page", page), ("404", escape)):
        head = raw.split("\r\n\r\n", 1)[0].lower()
        for header in ("content-length", "content-type"):
            assert head.count(header + ":") == 1, f"{name}: дубль заголовка {header}"


def test_settings_read_the_deployment_environment(monkeypatch):
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("VOICE_TOKEN", "from-env")
    monkeypatch.setenv("WORKSPACE_REPO", "https://github.com/example/repo.git")
    settings = Settings.from_env()
    assert (settings.port, settings.token) == (10000, "from-env")
    assert settings.workspace_repo.endswith("repo.git")


FAKE_SETUP = ["python3", str(Path(__file__).resolve().parent / "fake_setup_token.py")]


async def _auth_flow(code, token_env, tmp_path):
    settings = Settings(workspace=".", port=0, token="t", note_path=str(tmp_path / "i.md"))
    daemon = Daemon(settings)
    async with serve(daemon.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"id": "hello", "v": 1, "token": token_env}))
            first = json.loads(await ws.recv())
            if first.get("id") != "welcome":
                return first, None, None
            await ws.send(json.dumps({"id": "auth_start", "command": FAKE_SETUP}))
            url_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            await ws.send(json.dumps({"id": "auth_code", "code": code}))
            result = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            return first, url_msg, result


def test_subscription_can_be_connected_from_the_phone(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    welcome, url_msg, result = asyncio.run(_auth_flow("good-code", "t", tmp_path))
    assert welcome["credential"] == "none"
    assert url_msg["id"] == "auth_url"
    assert url_msg["url"].startswith("https://claude.com/cai/oauth/authorize")
    assert "redirect_uri=" in url_msg["url"]      # обрезанная ссылка ломает авторизацию
    assert "code_challenge=" in url_msg["url"] and "state=" in url_msg["url"]
    assert result["id"] == "auth_token"
    assert result["token"].startswith("sk-ant-oat01-")
    assert result["credential"] == "subscription"
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == result["token"]
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)


def test_a_wrong_code_reports_an_error_instead_of_a_token(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    _, _, result = asyncio.run(_auth_flow("nope", "t", tmp_path))
    assert result["id"] == "auth_error"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ


def test_setup_flow_requires_an_authenticated_client(tmp_path):
    welcome, _, _ = asyncio.run(_auth_flow("good-code", "wrong-token", tmp_path))
    assert welcome["id"] == "error" and welcome["code"] == "unauthorized"


def test_a_token_can_be_pasted_straight_into_the_app(tmp_path, monkeypatch):
    """У человека уже есть токен — заставлять его проходить OAuth незачем."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    env_file = tmp_path / "voice-shell.env"
    env_file.write_text("VOICE_TOKEN=t\n", encoding="utf-8")
    monkeypatch.setenv("VOICE_ENV_FILE", str(env_file))
    good = "sk-ant-oat01-" + "T" * 40

    async def flow(token):
        settings = Settings(workspace=".", port=0, token="t", note_path=str(tmp_path / "i.md"))
        daemon = Daemon(settings)
        async with serve(daemon.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"id": "hello", "v": 1, "token": "t"}))
                await ws.recv()
                await ws.send(json.dumps({"id": "auth_set", "token": token}))
                return json.loads(await asyncio.wait_for(ws.recv(), timeout=5))

    result = asyncio.run(flow(good))
    assert result["id"] == "auth_token"
    assert result["persisted"] is True
    assert result["credential"] == "subscription"
    assert good in env_file.read_text(encoding="utf-8")

    bad = asyncio.run(flow("не токен"))
    assert bad["id"] == "auth_error" and "не подошёл" in bad["message"]
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)


def test_undo_and_memory_are_answered_by_the_shell_itself(tmp_path, monkeypatch):
    """«Откати» и «запомни» не должны зависеть от того, занят ли Claude."""
    import subprocess

    from voice_claude import checkpoints

    workspace = tmp_path / "project"
    workspace.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True)
    (workspace / "auth.ts").write_text("было", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "первый"],
                   check=True, capture_output=True)

    async def flow():
        settings = Settings(workspace=str(workspace), port=0, token="t",
                            note_path=str(tmp_path / "i.md"))
        daemon = Daemon(settings)
        async with serve(daemon.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"id": "hello", "v": 1, "token": "t"}))
                await ws.recv()

                # Сымитируем правку, сделанную Claude.
                before = checkpoints.head(workspace)
                (workspace / "auth.ts").write_text("стало", encoding="utf-8")
                after = checkpoints.commit_all(workspace, "правка")
                daemon.journal.add(before, after, "почини auth.ts")

                said = []
                for phrase in ("запомни что я люблю короткие ответы",
                               "что ты обо мне помнишь",
                               "откати последнее"):
                    await ws.send(json.dumps(segment(phrase, MASTER)))
                    said.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=5)))
                return daemon, said

    daemon, said = asyncio.run(flow())
    assert said[0]["text"] == "Запомнил."
    assert "короткие ответы" in said[1]["text"]
    assert said[2]["text"].startswith("Откатил auth.ts")
    assert (workspace / "auth.ts").read_text(encoding="utf-8") == "было"
    assert daemon.journal.last() is None
    assert "короткие ответы" in daemon.memory.hint()
