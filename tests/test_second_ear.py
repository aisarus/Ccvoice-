"""Второе ухо: слышать, что вокруг, и пересказывать на своём языке.

Главное здесь — не пересказ, а граница. Реплика, которую человек оболочке не
адресовал, обязана дойти до буфера и не дойти больше никуда: цена ошибки —
выполненное действие по фразе из чужого разговора.
"""
import asyncio

import pytest

from voice_claude import i18n, lexicon
from voice_claude.ambient import AmbientBuffer
from voice_claude.server import Daemon, Settings


@pytest.fixture
def daemon(tmp_path):
    return Daemon(Settings(workspace=str(tmp_path), token="t"))


class Socket:
    """Сокет, которому достаточно помнить, что в него написали."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = None

    async def send(self, payload):
        import json
        self.sent.append(json.loads(payload))

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


def spoken(daemon) -> list[str]:
    return [p["text"] for c in daemon.clients for p in getattr(c, "sent", [])
            if p.get("id") == "voice_summary"]


def segment(text, **extra):
    return {"id": "speech_segment", "segment_id": "s", "transcript": text,
            "device": "phone_mic", "duration_ms": 1500, "voiced_frames": 30,
            "role": "master", **extra}


# -- включение голосом ------------------------------------------------------

@pytest.mark.parametrize("said", [
    "клод, второе ухо", "второе ухо", "клод, слушай вокруг",
    "claude, second ear", "claude, listen around",
])
def test_the_second_ear_opens_by_voice(said):
    assert lexicon.starts_with(said, lexicon.every("second_ear_on"))


@pytest.mark.parametrize("said", [
    "клод, выключи второе ухо", "хватит слушать вокруг",
    "claude, turn off the second ear",
])
def test_the_second_ear_closes_by_voice(said):
    assert lexicon.starts_with(said, lexicon.every("second_ear_off"))


def test_talking_about_the_second_ear_does_not_open_it():
    """«А что такое второе ухо?» — это вопрос, а не команда."""
    for said in ("а что такое второе ухо", "what is the second ear",
                 "расскажи про второе ухо"):
        assert not lexicon.starts_with(said, lexicon.every("second_ear_on"))


def test_opening_the_ear_also_opens_the_bystander_opt_in(daemon):
    """Иначе ухо открыто, а буфер копит пустоту: чужая речь отсекается
    отдельным флагом, и человек об этом не знает."""
    ws = Socket()
    daemon.clients.add(ws)
    asyncio.run(daemon._second_ear(True))
    assert daemon.ambient.enabled
    assert daemon.ambient.bystander_transcript
    assert i18n.t("ambient.on") in spoken(daemon)


def test_closing_the_ear_wipes_what_it_heard(daemon):
    ws = Socket()
    daemon.clients.add(ws)
    asyncio.run(daemon._second_ear(True))
    daemon.ambient.overhear("מתי זה יהיה מוכן")
    assert daemon.ambient.lines()

    asyncio.run(daemon._second_ear(False))
    assert not daemon.ambient.enabled
    assert daemon.ambient.lines() == []


def test_opening_an_open_ear_says_so_instead_of_reopening(daemon):
    ws = Socket()
    daemon.clients.add(ws)
    asyncio.run(daemon._second_ear(True))
    daemon.ambient.overhear("что-то услышанное")
    asyncio.run(daemon._second_ear(True))
    # Повторное открытие не должно стирать буфер как побочный эффект.
    assert daemon.ambient.lines()
    assert i18n.t("ambient.already_on") in spoken(daemon)


# -- граница: услышанное не исполняется -------------------------------------

def test_overheard_speech_reaches_the_buffer_and_stops_there(daemon):
    """Самое важное свойство второго уха.

    Фраза из чужого разговора может выглядеть как команда — «удали это»,
    «останови» — и классификатор говорящего может ошибиться. Поэтому решает
    не акустика, а факт: телефон знает, адресовали ему реплику или нет.
    """
    ws = Socket()
    daemon.clients.add(ws)
    asyncio.run(daemon._second_ear(True))
    before = list(spoken(daemon))

    asyncio.run(daemon._on_segment(ws, segment("откати последнее", ambient=True)))

    assert [line.text for line in daemon.ambient.lines()] == ["откати последнее"]
    assert spoken(daemon) == before, "услышанное вокруг дошло до исполнения"


def test_an_addressed_utterance_is_not_treated_as_overheard(daemon):
    ws = Socket()
    daemon.clients.add(ws)
    asyncio.run(daemon._second_ear(True))
    asyncio.run(daemon._on_segment(ws, segment("откати последнее")))
    assert i18n.t("undo.nothing") in spoken(daemon)


def test_nothing_is_buffered_while_the_ear_is_closed(daemon):
    """Буфер существует только когда ухо открыто — это его единственная
    гарантия приватности, и держится она здесь."""
    assert not daemon.ambient.enabled
    daemon.ambient.overhear("чужой разговор")
    assert daemon.ambient.lines() == []


# -- вопросы к буферу -------------------------------------------------------

@pytest.mark.parametrize("said", [
    "что он сейчас сказал", "какую цифру он назвал", "о чём мы договорились",
    "повтори последнее", "what did he say", "what did we agree",
])
def test_questions_to_the_buffer_are_recognised(said):
    assert AmbientBuffer.is_recall(said)


@pytest.mark.parametrize("said", ["почини тесты", "что такое вектор", "как дела"])
def test_ordinary_questions_are_not_buffer_questions(said):
    assert not AmbientBuffer.is_recall(said)


def test_asking_a_closed_ear_is_answered_without_waking_claude(daemon):
    """Круг к модели ради ответа «я ничего не слышал» — потраченные секунды
    в ухе и потраченный лимит подписки."""
    ws = Socket()
    daemon.clients.add(ws)
    asyncio.run(daemon._on_segment(ws, segment("что он сейчас сказал")))
    assert i18n.t("ambient.closed") in spoken(daemon)


def test_asking_an_empty_buffer_is_answered_the_same_way(daemon):
    ws = Socket()
    daemon.clients.add(ws)
    asyncio.run(daemon._second_ear(True))
    asyncio.run(daemon._on_segment(ws, segment("что он сейчас сказал")))
    assert i18n.t("ambient.nothing_heard") in spoken(daemon)


def test_the_buffer_is_handed_to_claude_with_roles_and_times(daemon):
    """Пересказывать должен Claude, а не оболочка: «что он сказал» про иврит —
    это перевод с пониманием контекста, а не выдача строки из буфера."""
    daemon.ambient.set_submode("passive")
    daemon.ambient.set_bystander_transcript(True)
    daemon.ambient.overhear("מתי זה יהיה מוכן")
    i18n.use("ru")

    rendered = daemon.ambient.transcript()
    assert "מתי זה יהיה מוכן" in rendered
    assert i18n.t("ambient.bystander") in rendered
