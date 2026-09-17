"""Поправка «не туда» должна превращаться в пример, а не пропадать."""
from voice_claude.learning import Examples, similarity


def test_a_correction_routes_the_same_phrase_next_time(tmp_path):
    examples = Examples(tmp_path)
    assert examples.suggest("посмотри что с деплоем") is None

    examples.remember("посмотри что с деплоем", "code")
    suggestion = examples.suggest("посмотри что с деплоем")
    assert suggestion is not None and suggestion[0] == "code"


def test_a_similar_phrase_also_counts(tmp_path):
    examples = Examples(tmp_path)
    examples.remember("посмотри что там с деплоем", "code")
    assert examples.suggest("посмотри что с деплоем")[0] == "code"


def test_an_unrelated_phrase_does_not(tmp_path):
    examples = Examples(tmp_path)
    examples.remember("посмотри что с деплоем", "code")
    assert examples.suggest("какая погода в Тель-Авиве") is None


def test_changing_your_mind_replaces_the_example(tmp_path):
    examples = Examples(tmp_path)
    examples.remember("расскажи про аутентификацию", "code")
    examples.remember("расскажи про аутентификацию", "chat")
    assert examples.suggest("расскажи про аутентификацию")[0] == "chat"
    assert len(examples) == 1


def test_examples_survive_a_restart(tmp_path):
    Examples(tmp_path).remember("собери релиз", "code")
    assert Examples(tmp_path).suggest("собери релиз")[0] == "code"


def test_similarity_ignores_filler_words():
    assert similarity("посмотри что с деплоем", "посмотри деплой") > 0.3
    assert similarity("почини тесты", "какая погода") == 0.0
