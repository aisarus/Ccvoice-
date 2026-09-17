"""Routing: an ambiguous utterance must never execute anything."""
import pytest

from voice_claude.router import Router
from voice_claude.spec import defaults


@pytest.fixture
def router():
    return Router()


def test_repo_work_goes_to_code(router):
    route = router.route("исправь ошибку в auth.ts и запусти тесты")
    assert route.target == "code"
    assert route.confidence >= 0.7


def test_plain_question_goes_to_chat(router):
    assert router.route("посчитай сколько будет семнадцать процентов от 4200").target == "chat"


def test_unclassifiable_utterance_falls_back_to_chat_not_code(router):
    route = router.route("ну такое себе, конечно")
    assert route.target == "chat"
    assert route.reason == "default"
    assert not router.is_mutating(route.target)


def test_explicit_prefix_wins_and_is_stripped(router):
    route = router.route("в код добей и закоммить")
    assert (route.target, route.reason) == ("code", "explicit_prefix")
    assert route.text == "добей и закоммить"


def test_note_prefix_captures_without_answering(router):
    assert router.route("запиши идею про второе ухо").target == "note"


def test_sticky_target_holds_inside_the_conversation_window(router):
    router.route("в код почини билд")
    route = router.route("продолжай", ms_since_last=4000)
    assert (route.target, route.reason) == ("code", "sticky")


def test_sticky_target_expires_after_the_window(router):
    router.route("в код почини билд")
    route = router.route("а что думаешь про отпуск", ms_since_last=60_000)
    assert route.target == "chat"


def test_misroute_recovery_clears_the_sticky_target(router):
    router.route("в код почини билд")
    assert router.is_misroute_recovery("не туда, я не про код")
    router.sticky_target = None
    assert router.route("ну такое себе").target == "chat"


def test_handoff_is_detected_both_ways(router):
    assert router.handoff("перекинь это в код") is not None
    assert router.handoff("объясни попроще") is not None
    assert router.handoff("просто фраза") is None


def test_earcons_differ_per_target(router):
    assert router.earcon_for("code") != router.earcon_for("chat")


def test_forced_target_holds_outside_the_conversation_window(router):
    """Чип в интерфейсе — явный выбор: он не должен истекать вместе с окном."""
    router.force("code")
    route = router.route("ну такое себе", ms_since_last=10 ** 6)
    assert (route.target, route.reason) == ("code", "forced")


def test_auto_clears_the_forced_target(router):
    router.force("code")
    router.force(None)
    assert router.route("ну такое себе", ms_since_last=10 ** 6).target == "chat"


def test_spoken_prefix_still_wins_over_a_forced_target(router):
    router.force("code")
    route = router.route("в чат что думаешь про отпуск")
    assert (route.target, route.reason) == ("chat", "explicit_prefix")


def test_unknown_forced_target_is_rejected(router):
    import pytest as _pytest
    with _pytest.raises(ValueError):
        router.force("nope")


def test_chat_stays_non_mutating_even_with_web_access(router):
    """Поиск — это чтение: цель остаётся безопасным дефолтом."""
    assert not router.is_mutating("chat")
    assert router.is_mutating("code")


def test_ordinary_work_phrases_reach_the_code_target(router):
    """Живая речь редко содержит слово «git» — она говорит «файлы в проекте»."""
    for phrase in ("покажи какие файлы в проекте",
                   "что лежит в конфиге",
                   "посмотри логи сервера",
                   "расскажи про аутентификацию в проекте"):
        route = router.route(phrase, ms_since_last=10 ** 6)
        router.sticky_target = None
        assert route.target == "code", f"{phrase} уехало в {route.target}"


def test_talk_is_still_talk(router):
    """Расширение словаря не должно утащить в код обычный разговор."""
    for phrase in ("как думаешь, стоит ли переезжать на Rust",
                   "что такое вектор эмбеддинга",
                   "посчитай сколько будет семнадцать процентов от 4200",
                   "напиши письмо Игорю про перенос встречи"):
        route = router.route(phrase, ms_since_last=10 ** 6)
        router.sticky_target = None
        assert route.target == "chat", f"{phrase} уехало в {route.target}"


def test_short_words_do_not_leak_into_code(router):
    """«лог» внутри «логично» и «порт» внутри «спорт» — не про работу."""
    for phrase in ("это же логично, правда",
                   "давай обсудим спорт",
                   "импорт данных из таблицы",
                   "классно получилось",
                   "какой у нас диалог получился",
                   "расскажи про кодекс чести"):
        route = router.route(phrase, ms_since_last=10 ** 6)
        router.sticky_target = None
        assert route.target == "chat", f"{phrase} уехало в {route.target}"


def test_short_words_still_work_as_whole_words(router):
    """Но «на каком порту» и «покажи класс роутера» — про работу."""
    for phrase in ("на каком порту крутится демон",
                   "покажи класс роутера",
                   "покажи код функции route",
                   "посмотри логи сервера"):
        route = router.route(phrase, ms_since_last=10 ** 6)
        router.sticky_target = None
        assert route.target == "code", f"{phrase} уехало в {route.target}"


def test_one_word_prefixes_are_enough(router):
    """«Код» и «чат» — короче, чем «в код», и в речи удобнее."""
    code = router.route("код почини падающий тест", ms_since_last=10 ** 6)
    assert (code.target, code.reason) == ("code", "explicit_prefix")
    assert code.text == "почини падающий тест"

    chat = router.route("чат что такое вектор эмбеддинга", ms_since_last=10 ** 6)
    assert (chat.target, chat.reason) == ("chat", "explicit_prefix")
    assert chat.text == "что такое вектор эмбеддинга"


def test_the_model_is_asked_only_where_the_lexicon_is_lost(router):
    """Решение человека не переспрашивают: префикс, чип, поправка, окно."""
    router.sticky_target = None
    unknown = router.route("сделай чтобы форма не отправлялась дважды",
                           ms_since_last=10 ** 6)
    assert unknown.reason == "default"
    assert router.needs_intent_model(unknown)

    router.sticky_target = None
    prefix = router.route("код почини тест", ms_since_last=10 ** 6)
    assert not router.needs_intent_model(prefix)

    router.sticky_target = None
    learned = router.route("развёрнутый ответ", ms_since_last=10 ** 6,
                           learned=("code", 0.9))
    assert not router.needs_intent_model(learned)

    router.sticky_target = "code"
    sticky = router.route("дальше", ms_since_last=1000)
    assert sticky.reason == "sticky" and not router.needs_intent_model(sticky)

    router.sticky_target = None
    obvious = router.route("закоммить и запушь ветку", ms_since_last=10 ** 6)
    assert obvious.confidence >= 0.8 and not router.needs_intent_model(obvious)


def test_the_model_can_be_switched_off(router):
    """Сеть может быть дорога или недоступна — тогда только словарь."""
    off = Router(config={**defaults("targets"), "intent_model": "off"})
    route = off.route("сделай чтобы форма не отправлялась дважды", ms_since_last=10 ** 6)
    assert not off.needs_intent_model(route)


def test_a_strange_answer_from_the_model_is_ignored(router):
    """Модель отвечает одним словом; всё прочее — не повод менять цель."""
    route = router.route("сделай чтобы форма не отправлялась дважды", ms_since_last=10 ** 6)
    assert router.apply_intent(route, "не-цель") is route
    decided = router.apply_intent(route, "code")
    assert (decided.target, decided.reason) == ("code", "intent_model")
    assert router.sticky_target == "code"


def test_a_plain_question_breaks_out_of_the_previous_target(router):
    """Живая проверка показала: «что такое …» сразу после работы с кодом
    уезжало в код по липкости."""
    work = router.route("покажи какие файлы в проекте", ms_since_last=10 ** 6)
    assert work.target == "code"
    question = router.route("что такое вектор эмбеддинга", ms_since_last=2000)
    assert (question.target, question.reason) == ("chat", "classifier")


def test_short_continuations_still_stick(router):
    """«Дальше» и «добей» — это продолжение работы, а не смена цели."""
    router.route("почини падающий тест", ms_since_last=10 ** 6)
    for phrase in ("дальше", "продолжай", "добей"):
        route = router.route(phrase, ms_since_last=2000)
        assert route.target == "code", f"{phrase} уехало в {route.target}"
