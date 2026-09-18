"""The shell speaks four languages and listens in all of them at once.

These tests hold the line that made localisation worth doing: a person who
does not speak Russian gets the same shell, not a voice pipe with the commands
filed off. So they check the catalogue for holes, the phrase tables for every
command the daemon dispatches on, and the rule that decides which language an
answer comes back in.
"""
import re
from pathlib import Path

import pytest

from voice_claude import (checkpoints, formatter, i18n, lexicon, memory, tasks,
                          telegram)
from voice_claude.router import Router

DAEMON = Path(__file__).resolve().parents[1] / "daemon" / "voice_claude"
PLACEHOLDER = re.compile(r"\{(\w+)")


# -- the catalogue ---------------------------------------------------------

def test_every_phrase_exists_in_every_language():
    """A gap here is a person hearing Russian at an English phone."""
    assert i18n.missing_translations() == {}


@pytest.mark.parametrize("key", sorted(i18n.PHRASES))
def test_translations_take_the_same_placeholders(key):
    """A missing `{reason}` in one language is a KeyError only that locale hits.

    The English wording is the reference, so the others have to fill exactly
    the same slots — no more (nothing to fill it with) and no fewer (the
    sentence loses the part that carried the news).
    """
    entry = i18n.PHRASES[key]
    reference = set(PLACEHOLDER.findall(entry["en"]))
    for lang, text in entry.items():
        assert set(PLACEHOLDER.findall(text)) == reference, f"{key} / {lang}"


def test_every_key_the_daemon_asks_for_is_in_the_catalogue():
    """Catches a `t("undo.dine")` typo, which otherwise fails only out loud."""
    used = set()
    for path in DAEMON.glob("*.py"):
        if path.name == "i18n.py":
            continue
        used |= set(re.findall(r'(?<!\w)t\(\s*"([a-z][\w.]*)"', path.read_text("utf-8")))
    assert used, "the scan found nothing — it stopped matching the call shape"
    assert used <= set(i18n.PHRASES), sorted(used - set(i18n.PHRASES))


def test_an_untranslated_language_falls_back_to_a_real_sentence():
    """Better a right sentence in the wrong language than a bare key."""
    i18n.PHRASES["test.only_english"] = {"en": "Only English here."}
    try:
        i18n.use("zh")
        assert i18n.t("test.only_english") == "Only English here."
    finally:
        del i18n.PHRASES["test.only_english"]


# -- choosing the language -------------------------------------------------

@pytest.mark.parametrize("tag,expected", [
    ("ru-RU", "ru"), ("en_US", "en"), ("zh-Hans-CN", "zh"), ("es-419", "es"),
    ("ru", "ru"), ("", None), (None, None), ("he-IL", None), ("klingon", None),
])
def test_language_tags_are_normalised(tag, expected):
    assert i18n.normalize(tag) == expected


@pytest.mark.parametrize("text,expected", [
    ("откати последнее", "ru"),
    ("undo the last thing please", "en"),
    ("deshaz el último archivo por favor", "es"),
    ("撤销刚才的改动", "zh"),
    ("42", None),
])
def test_the_spoken_language_is_recognised(text, expected):
    assert i18n.detect(text) == expected


def test_an_unknown_tag_leaves_the_language_alone():
    i18n.use("ru")
    i18n.use("he-IL")
    assert i18n.current() == "ru"


def test_answering_in_another_language_is_temporary():
    i18n.use("ru")
    with i18n.speaking("es"):
        assert i18n.t("note.saved") == "Anotado."
    assert i18n.t("note.saved") == "Записал."


def test_stress_marks_are_a_russian_thing():
    """`+` before the stressed vowel is read aloud as a plus anywhere else."""
    i18n.use("ru")
    assert i18n.uses_stress_marks()
    for lang in ("en", "es", "zh"):
        i18n.use(lang)
        assert not i18n.uses_stress_marks()


# -- listening in every language at once -----------------------------------

@pytest.mark.parametrize("said", [
    "откати последнее", "claude, undo that", "oye, deshaz eso", "撤销刚才的",
])
def test_undo_is_heard_in_every_language(said):
    assert checkpoints.matches(said, checkpoints.UNDO_PHRASES)


@pytest.mark.parametrize("said", [
    "а это вообще можно откатить?",
    "can that be undone at all?",
    "¿se puede deshacer eso?",
])
def test_talking_about_an_undo_is_not_an_undo(said):
    """The guard that kept a question from rolling the project back holds in
    the new languages too — it is the one place where hearing four languages
    at once could have cost something."""
    assert not checkpoints.matches(said, checkpoints.UNDO_PHRASES)


@pytest.mark.parametrize("said,fact", [
    ("запомни что я работаю по ночам", "я работаю по ночам"),
    ("remember that I work at night", "I work at night"),
    ("recuerda que trabajo de noche", "trabajo de noche"),
    ("记住我晚上工作", "我晚上工作"),
])
def test_remembering_works_in_every_language(said, fact):
    assert memory.remember_intent(said) == fact


@pytest.mark.parametrize("said", [
    "в фоне почини тесты", "in the background fix the tests",
    "en segundo plano arregla las pruebas", "后台把测试修一下",
])
def test_a_background_task_is_asked_for_in_every_language(said):
    assert tasks.background_request(said)


@pytest.mark.parametrize("said", [
    "чем занят", "what are you working on", "qué estás haciendo", "你在做什么",
])
def test_the_status_question_is_heard_in_every_language(said):
    assert tasks.is_status_question(said)


@pytest.mark.parametrize("said,wanted", [
    ("скинь мне в телегу конфиг", "конфиг"),
    ("send me the config on telegram", "config"),
    ("mándame el config por telegram", "config"),
    ("把配置发到电报", "配置"),
])
def test_the_telegram_bridge_is_asked_in_every_language(said, wanted):
    assert telegram.share_request(said) == wanted


@pytest.mark.parametrize("said", [
    "почини тесты в auth.ts", "fix the failing tests in auth.ts",
    "arregla las pruebas de auth.ts", "修复 auth.ts 里的测试",
])
def test_work_is_routed_to_code_in_every_language(said):
    router = Router()
    assert router.route(said).target == "code"


@pytest.mark.parametrize("said", [
    "что такое вектор эмбеддинга", "what is an embedding vector",
    "qué es un vector de embedding", "什么是嵌入向量",
])
def test_a_question_is_routed_to_chat_in_every_language(said):
    router = Router()
    assert router.route(said).target == "chat"


@pytest.mark.parametrize("said", [
    "код покажи логи", "code show me the logs", "código muestra los logs",
])
def test_the_spoken_prefix_works_in_every_language(said):
    router = Router()
    route = router.route(said)
    assert route.target == "code" and route.reason == "explicit_prefix"


@pytest.mark.parametrize("said", ["кодекс чести", "codebase philosophy"])
def test_a_word_that_merely_starts_like_a_prefix_is_not_one(said):
    """«кодекс» is not «код», and «codebase» is not «code»: a prefix has to be
    a whole word, or an idle remark ends up executing in the project."""
    router = Router()
    assert router.route(said).reason != "explicit_prefix"


@pytest.mark.parametrize("said,action", [
    ("да", "approve_once"), ("go ahead", "approve_once"),
    ("sí", "approve_once"), ("可以", "approve_once"),
    ("нет", "reject"), ("no lo hagas", "reject"), ("不要", "reject"),
    ("да, и больше не спрашивай для этой команды", "approve_and_remember_rule"),
    ("yes and stop asking for this command", "approve_and_remember_rule"),
])
def test_permission_answers_are_understood_in_every_language(said, action):
    assert formatter.parse_approval(said) == action


def test_apostrophes_survive_normalisation():
    """«don't» and «what's» are command phrases; «don t» is not."""
    assert lexicon.normalise("Don't — what's done?") == "don't what's done"


# -- долгие сессии и смена языка -------------------------------------------

class FakeSdkClient:
    """Считает, сколько раз сессию поднимали заново, и с каким промптом."""

    built: list["FakeSdkClient"] = []

    def __init__(self, options=None):
        self.options = options
        self.connected = False
        FakeSdkClient.built.append(self)

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False


@pytest.fixture
def fake_sdk(monkeypatch):
    """Подставляет claude_agent_sdk, которого в тестовой среде нет."""
    import sys
    import types

    from voice_claude import targets

    module = types.ModuleType("claude_agent_sdk")
    module.ClaudeSDKClient = FakeSdkClient
    module.ClaudeAgentOptions = lambda **kw: kw
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)
    monkeypatch.setattr(targets, "sdk_available", lambda: True)
    FakeSdkClient.built.clear()
    return targets


def test_switching_language_rebuilds_the_session_that_carries_the_prompt(fake_sdk, tmp_path):
    """Системный промпт живёт столько же, сколько сессия.

    Человек, перешедший на другой язык, получал верную работу и пересказ
    вслух на прежнем: сессия пересказчика всё ещё держала промпт, с которым
    её подняли. Смена языка — единственный повод поднять её заново.
    """
    import asyncio

    target = fake_sdk.SummaryTarget(tmp_path / "summary")

    i18n.use("ru")
    asyncio.run(target.connect())
    asyncio.run(target.connect())
    assert len(FakeSdkClient.built) == 1, "сессия поднималась на каждую реплику"
    assert "сокращаешь" in FakeSdkClient.built[0].options["system_prompt"]

    i18n.use("en")
    asyncio.run(target.connect())
    assert len(FakeSdkClient.built) == 2, "сессия осталась с прежним промптом"
    prompt = FakeSdkClient.built[1].options["system_prompt"]
    assert i18n.t("prompt.summary_system") in prompt
    assert prompt.endswith(i18n.t("prompt.answer_language"))

    asyncio.run(target.connect())
    assert len(FakeSdkClient.built) == 2, "сессию роняет язык, а не каждая реплика"


def test_the_code_session_is_not_dropped_when_the_language_changes(fake_sdk, tmp_path):
    """Преамбула кодовой цели идёт с каждой репликой, а не с сессией. Ронять
    долгую сессию Claude Code из-за языка — значит терять весь контекст
    работы на ровном месте."""
    import asyncio

    target = fake_sdk.CodeTarget(tmp_path / "code")

    i18n.use("ru")
    asyncio.run(target.connect())
    i18n.use("zh")
    asyncio.run(target.connect())
    assert len(FakeSdkClient.built) == 1


# -- закреплённый язык ответа ----------------------------------------------

def _daemon():
    from voice_claude.server import Daemon, Settings
    return Daemon(Settings(workspace="/tmp", token="t"))


def test_a_pinned_reply_language_beats_the_language_that_was_spoken():
    """«Клод, английский» — это просьба, а не описание.

    Человек говорит по-русски и хочет слышать английский: распознаватель
    Android слушает один язык за раз, и менять его вслед за ответом значило
    бы оглохнуть на том, на котором только что говорили.
    """
    daemon = _daemon()
    daemon._pin_language("en-US")
    i18n.use(daemon._reply_language or i18n.detect("почини тесты"))
    assert i18n.current() == "en"


def test_unpinning_goes_back_to_answering_in_the_language_heard():
    daemon = _daemon()
    daemon._pin_language("en-US")
    daemon._pin_language("")
    assert daemon._reply_language is None
    i18n.use(daemon._reply_language or i18n.detect("почини тесты"))
    assert i18n.current() == "ru"


def test_auto_unpins_the_same_way_an_empty_value_does():
    """Снять закрепление голосом должно быть так же просто, как поставить."""
    daemon = _daemon()
    daemon._pin_language("ru")
    daemon._pin_language("auto")
    assert daemon._reply_language is None


def test_an_unknown_language_does_not_silently_unpin():
    """Иначе «Клод, суахили» тихо снимал бы закрепление, а человек думал бы,
    что переключился."""
    daemon = _daemon()
    daemon._pin_language("en-US")
    daemon._pin_language("sw-KE")
    assert daemon._reply_language == "en"
