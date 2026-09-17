"""Проактивность не должна превращаться в назойливость."""
import time

from voice_claude import watcher
from voice_claude.watcher import Event, Watcher


def one(key="run-1"):
    return [Event(key=key, text="Упала сборка android.", fix_prompt="почини")]


def test_a_failure_is_announced_once(tmp_path, monkeypatch):
    monkeypatch.setenv("PROACTIVE", "watch")
    monkeypatch.setenv("QUIET_HOURS", "off")
    seen = Watcher(tmp_path, poll=one)
    assert [e.text for e in seen.check(now=0)] == ["Упала сборка android."]
    assert seen.check(now=100) == []


def test_silence_at_night(tmp_path, monkeypatch):
    monkeypatch.setenv("PROACTIVE", "watch")
    monkeypatch.setenv("QUIET_HOURS", "23-8")
    night = time.struct_time((2026, 9, 17, 3, 0, 0, 0, 260, 0))
    assert Watcher(tmp_path, poll=one).check(now=0, clock=night) == []

    day = time.struct_time((2026, 9, 17, 14, 0, 0, 0, 260, 0))
    assert Watcher(tmp_path, poll=one).check(now=0, clock=day)


def test_off_means_off(tmp_path, monkeypatch):
    monkeypatch.setenv("PROACTIVE", "off")
    monkeypatch.setenv("QUIET_HOURS", "off")
    assert Watcher(tmp_path, poll=one).check(now=0) == []


def test_not_more_than_twice_in_five_minutes(tmp_path, monkeypatch):
    monkeypatch.setenv("PROACTIVE", "watch")
    monkeypatch.setenv("QUIET_HOURS", "off")
    events = [Event(key=f"run-{i}", text=f"сборка {i}") for i in range(5)]
    seen = Watcher(tmp_path, poll=lambda: events)
    spoken = seen.check(now=0)
    assert len(spoken) == 1                      # остальные не проходят минимальный разрыв


def test_quiet_window_arithmetic():
    night = time.struct_time((2026, 9, 17, 2, 0, 0, 0, 260, 0))
    noon = time.struct_time((2026, 9, 17, 12, 0, 0, 0, 260, 0))
    assert watcher.is_quiet(night, (23, 8))
    assert not watcher.is_quiet(noon, (23, 8))
    assert watcher.is_quiet(noon, (9, 18))       # окно внутри одних суток


def test_news_waits_for_someone_to_listen(tmp_path, monkeypatch):
    """Телефон спит — новость не пропадает, а ждёт подключения."""
    import asyncio
    import json

    from voice_claude.server import Daemon, Settings

    monkeypatch.setenv("PROACTIVE", "watch")
    monkeypatch.setenv("QUIET_HOURS", "off")

    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send(self, raw):
            self.sent.append(json.loads(raw))

    async def flow():
        daemon = Daemon(Settings(workspace=str(tmp_path), port=0, token="t",
                                 note_path=str(tmp_path / "i.md")))
        await daemon._announce("Упала сборка android.")
        ws = FakeWS()
        await daemon._dispatch(ws, {"id": "hello", "v": 1, "token": "t"})
        return ws

    ws = asyncio.run(flow())
    spoken = [m for m in ws.sent if m.get("id") == "voice_summary"]
    assert spoken and spoken[0]["text"] == "Упала сборка android."
    assert spoken[0]["proactive"] is True
    assert [m for m in ws.sent if m.get("id") == "welcome"]
