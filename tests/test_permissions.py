"""Голосовые подтверждения: пока висит запрос, реплика — ответ на него."""
import asyncio
import json

import pytest

from voice_claude.formatter import approval_to_speech, parse_approval
from voice_claude.server import Daemon, Settings

MASTER = {"level_rel_db": 1.0, "snr_db": 30.0, "drr_db": 10.0, "c50_db": 14.0,
          "hf_ratio_db": 1.0, "lf_proximity_db": 4.0}
BYSTANDER = {"level_rel_db": -14.0, "snr_db": 9.0, "drr_db": -1.0, "c50_db": 2.0,
             "hf_ratio_db": -7.0, "lf_proximity_db": -2.0}


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    def kinds(self, kind):
        return [m for m in self.sent if m.get("id") == kind]


def segment(text, features):
    return {"id": "speech_segment", "segment_id": "s", "transcript": text, "device": "phone_mic",
            "duration_ms": 1400, "voiced_frames": 45, "features": features}


async def _answer(text, features, tmp_path):
    daemon = Daemon(Settings(workspace=".", port=0, token="t", note_path=str(tmp_path / "i.md")))
    ws = FakeWS()
    daemon.clients.add(ws)
    future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    daemon._pending["req1"] = future
    daemon._pending_tools["req1"] = "Bash rm -rf ./build"
    await daemon._dispatch(ws, segment(text, features))
    result = future.result() if future.done() else None
    return daemon, ws, result


@pytest.mark.parametrize("phrase,expected", [
    ("да", "approve_once"),
    ("давай", "approve_once"),
    ("нет, не надо", "reject"),
    ("да, и больше не спрашивай для этой команды", "approve_and_remember_rule"),
    ("что именно?", "speak_details"),
    ("почини тесты", None),
])
def test_approval_phrases_are_parsed(phrase, expected):
    assert parse_approval(phrase) == expected


def test_yes_approves_the_pending_request(tmp_path):
    _, ws, result = asyncio.run(_answer("да", MASTER, tmp_path))
    assert result is True
    assert ws.kinds("permission_result")[0]["approved"] is True


def test_no_rejects_it(tmp_path):
    _, ws, result = asyncio.run(_answer("нет, не надо", MASTER, tmp_path))
    assert result is False
    assert ws.kinds("permission_result")[0]["approved"] is False


def test_remembering_the_rule_preapproves_the_tool(tmp_path):
    daemon, ws, result = asyncio.run(
        _answer("да, и больше не спрашивай для этой команды", MASTER, tmp_path))
    assert result is True
    assert ws.kinds("permission_result")[0]["remembered"] is True
    assert "Bash rm -rf ./build" in daemon.preapproved


def test_a_bystander_cannot_approve(tmp_path):
    _, ws, result = asyncio.run(_answer("да", BYSTANDER, tmp_path))
    assert result is None                      # запрос остаётся висеть
    assert ws.kinds("route")[0]["reason"] == "approval_role_gate"


def test_an_unrelated_utterance_does_not_execute_while_waiting(tmp_path):
    _, ws, result = asyncio.run(_answer("почини auth.ts и закоммить", MASTER, tmp_path))
    assert result is None
    assert ws.kinds("route")[0]["reason"] == "awaiting_permission"
    assert not ws.kinds("voice_summary")


def test_details_are_spoken_without_deciding(tmp_path):
    _, ws, result = asyncio.run(_answer("что именно?", MASTER, tmp_path))
    assert result is None
    assert "rm -rf ./build" in ws.kinds("voice_summary")[0]["text"]


def test_request_is_phrased_like_a_human():
    assert approval_to_speech("rm -rf ./build") == "Клод хочет удалить старую папку build. Разрешить?"


def test_stop_work_interrupts_and_says_so(tmp_path):
    """«стоп» гасит голос, «останови работу» — саму работу."""
    async def flow():
        daemon = Daemon(Settings(workspace=".", port=0, token="t", note_path=str(tmp_path / "i.md")))
        ws = FakeWS()
        daemon.clients.add(ws)
        await daemon._dispatch(ws, {"id": "interrupt", "scope": "work"})
        voice_only = FakeWS()
        daemon.clients.add(voice_only)
        await daemon._dispatch(voice_only, {"id": "interrupt", "scope": "voice"})
        return ws, voice_only

    ws, voice_only = asyncio.run(flow())
    said = [m for m in ws.sent if m.get("id") == "voice_summary"]
    assert said and said[0]["text"] == "Остановил."
    # у второго клиента подключения ещё не было к моменту первой команды
    assert not [m for m in voice_only.sent if m.get("id") == "voice_summary"]
    assert [m for m in voice_only.sent if m.get("id") == "state"]


def test_long_work_says_so_instead_of_going_silent(tmp_path):
    """Тишина в наушнике неотличима от поломки."""
    async def flow():
        daemon = Daemon(Settings(workspace=".", port=0, token="t",
                                 note_path=str(tmp_path / "i.md"),
                                 first_ack_s=0.05, progress_gap_s=0.05))
        ws = FakeWS()
        daemon.clients.add(ws)

        async def slow():
            await asyncio.sleep(0.2)
            return "готово"

        result = await daemon._await_with_progress(slow())
        return result, ws

    result, ws = asyncio.run(flow())
    spoken = [m["text"] for m in ws.sent if m.get("id") == "voice_summary"]
    assert result == "готово"
    assert spoken[0] == "Работаю."
    assert "Ещё работаю." in spoken[1:]
    assert all(m.get("progress") for m in ws.sent if m.get("id") == "voice_summary")


def test_a_quick_answer_says_nothing_extra(tmp_path):
    async def flow():
        daemon = Daemon(Settings(workspace=".", port=0, token="t",
                                 note_path=str(tmp_path / "i.md"),
                                 first_ack_s=5.0, progress_gap_s=5.0))
        ws = FakeWS()
        daemon.clients.add(ws)

        async def quick():
            return "быстро"

        return await daemon._await_with_progress(quick()), ws

    result, ws = asyncio.run(flow())
    assert result == "быстро"
    assert not [m for m in ws.sent if m.get("id") == "voice_summary"]
