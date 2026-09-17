"""Долговременная память: голосом пополняется, файлом читается."""
from voice_claude import memory


def test_facts_are_appended_and_read_back(tmp_path):
    store = memory.Memory(tmp_path)
    assert store.remember("Арсен работает по ночам") == "Арсен работает по ночам"
    store.remember("проект Aegis — главный")
    assert store.facts() == ["Арсен работает по ночам", "проект Aegis — главный"]
    assert "Aegis" in store.hint()


def test_the_same_fact_is_not_doubled(tmp_path):
    store = memory.Memory(tmp_path)
    store.remember("любит короткие ответы")
    store.remember("Любит короткие ответы")
    assert len(store.facts()) == 1


def test_forgetting_removes_only_what_matched(tmp_path):
    store = memory.Memory(tmp_path)
    store.remember("проект Aegis — главный")
    store.remember("сервер называется bot-server")
    assert store.forget("aegis") == 1
    assert store.facts() == ["сервер называется bot-server"]


def test_memory_is_a_readable_file(tmp_path):
    store = memory.Memory(tmp_path)
    store.remember("говорит по-русски и на иврите")
    written = store.path.read_text(encoding="utf-8")
    assert written.startswith("# Память Voice Shell")
    assert "- говорит по-русски и на иврите" in written


def test_empty_memory_adds_nothing_to_the_prompt(tmp_path):
    assert memory.Memory(tmp_path).hint() == ""


def test_voice_commands_are_parsed():
    # «запомни что» съедается целиком: в факт попадает только сам факт.
    assert memory.remember_intent("запомни что я предпочитаю короткие ответы") \
        == "я предпочитаю короткие ответы"
    assert memory.remember_intent("Имей в виду, сервер зовут bot-server") \
        == "сервер зовут bot-server"
    assert memory.remember_intent("почини тесты") is None
    assert memory.forget_intent("забудь про Aegis") == "Aegis"
    assert memory.recall_intent("что ты обо мне помнишь")
    assert not memory.recall_intent("что там с тестами")


def test_forgetting_works_on_the_word_not_on_the_exact_letters(tmp_path):
    """«Забудь про ночи» не убирало факт «работаю по ночам»: искалось
    буквальное совпадение, а человек говорит в другом падеже."""
    store = memory.Memory(tmp_path)
    store.remember("работаю по ночам")
    store.remember("проект называется Эгида")

    assert store.forget("ночи") == 1
    assert store.facts() == ["проект называется Эгида"]
    # Чужое слово с общим началом факт не уносит.
    assert store.forget("проектор") == 0
