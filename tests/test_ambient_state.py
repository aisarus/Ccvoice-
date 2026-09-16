"""Ambient buffer and the conversation window."""
import pytest

from voice_claude.ambient import AmbientBuffer, RateLimiter, WhisperGate
from voice_claude.state import IDLE, LISTENING, Machine


def test_ambient_is_off_by_default():
    assert not AmbientBuffer().enabled


def test_passive_keeps_master_lines_and_drops_bystanders_by_default():
    buf = AmbientBuffer(submode="passive")
    assert buf.add("master", "мне нужен дедлайн")
    assert not buf.add("bystander", "чужой разговор")
    assert not buf.add("self_echo", "это наш собственный tts")
    assert len(buf.lines()) == 1


def test_bystander_lines_are_kept_only_when_explicitly_enabled():
    buf = AmbientBuffer(submode="passive", config={"bystander_transcript": True})
    assert buf.add("bystander", "он назвал цифру сорок два")


def test_passive_never_leaves_the_device():
    assert not AmbientBuffer(submode="passive").leaves_device()
    assert AmbientBuffer(submode="assist").leaves_device()


def test_buffer_expires_beyond_retention():
    buf = AmbientBuffer(submode="passive", config={"buffer_min": 1})
    buf.add("master", "старое", now=1000.0)
    buf.add("master", "свежее", now=1000.0 + 59)
    assert [line.text for line in buf.lines(now=1000.0 + 61)] == ["свежее"]


def test_switching_off_wipes_the_buffer():
    buf = AmbientBuffer(submode="passive")
    buf.add("master", "секрет")
    buf.set_submode("off")
    assert buf.lines() == []


def test_recall_questions_are_recognised():
    assert AmbientBuffer.is_recall("Клод, что он только что сказал?")
    assert AmbientBuffer.is_recall("какую цифру он назвал")
    assert not AmbientBuffer.is_recall("почини auth.ts и закоммить")


def test_whisper_gate_respects_silence_and_the_master():
    gate = WhisperGate()
    assert not gate.may_speak(master_speaking=True, silence_ms=5000)
    assert not gate.may_speak(master_speaking=False, silence_ms=300)
    assert gate.may_speak(master_speaking=False, silence_ms=1500)


def test_whisper_output_is_short():
    gate = WhisperGate()
    long = " ".join(["слово"] * 40)
    assert len(gate.shorten(long).split()) <= gate.max_words


def test_proactive_rate_limit():
    limiter = RateLimiter(max_per_window=2, window_s=300, min_gap_s=60)
    assert limiter.allow(now=0)
    assert not limiter.allow(now=30)      # too soon
    assert limiter.allow(now=120)
    assert not limiter.allow(now=200)     # window budget spent


def test_conversation_window_opens_and_expires():
    machine = Machine()
    machine.open_window(now=0.0)
    assert machine.window_open(now=10.0)
    assert not machine.needs_wake_word(now=10.0)
    assert not machine.window_open(now=20.0)
    assert machine.needs_wake_word(now=20.0)


def test_unknown_state_is_rejected():
    machine = Machine()
    machine.to(LISTENING)
    with pytest.raises(ValueError):
        machine.to("BUSY")
    machine.to(IDLE)
