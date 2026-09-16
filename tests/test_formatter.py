"""Voice formatter: full output on screen, 1-3 sentences in the ear."""
from voice_claude.formatter import approval_to_speech, summarize

CLAUDE_OUTPUT = """Modified:
src/auth/login.ts
src/auth/session.ts
tests/auth.test.ts

Found a missing userId in the fixture...

Ran npm test...
47 tests passed
"""


def test_spec_example_is_reproduced():
    summary = summarize(CLAUDE_OUTPUT)
    assert summary.text == "Исправил login и session. Все 47 тестов проходят."


def test_summary_never_exceeds_three_sentences():
    noisy = "\n".join(f"Строка вывода номер {i}. Ещё предложение {i}." for i in range(40))
    assert summarize(noisy).sentences <= 3


def test_exact_counts_are_preserved():
    assert "47" in summarize(CLAUDE_OUTPUT).text


def test_failure_count_wins_over_success_line():
    out = "Ran pytest...\n3 тестов падают\n44 тестов проходят"
    assert "3 тестов падают." in summarize(out).text


def test_question_is_kept_and_flagged():
    out = "В fixture отсутствует userId. Могу исправить. Делать?"
    summary = summarize(out)
    assert summary.is_question
    assert summary.text.endswith("?")


def test_file_paths_are_not_read_aloud():
    spoken = summarize(CLAUDE_OUTPUT).text
    assert "src/" not in spoken and ".ts" not in spoken


def test_llm_hook_replaces_the_rule_based_path():
    summary = summarize(CLAUDE_OUTPUT, llm=lambda _: "Готово, всё зелёное.")
    assert summary.text == "Готово, всё зелёное."


def test_approval_is_spoken_like_a_human():
    assert approval_to_speech("rm -rf ./build") == "Клод хочет удалить старую папку build. Разрешить?"
    assert approval_to_speech("git push origin main").startswith("Клод хочет запушить")
    assert "Разрешить?" in approval_to_speech("Bash {\"command\": \"curl example.com\"}")


CODE_HEAVY = """Сейчас проверю тесты.

```bash
npm test -- --coverage
```

$ git status
src/auth/login.ts
Ran npm test...
47 tests passed
"""


def test_commands_and_code_blocks_are_never_spoken():
    spoken = summarize(CODE_HEAVY).text
    for forbidden in ("npm test", "git status", "--coverage", "```", "src/"):
        assert forbidden not in spoken
    assert "47" in spoken


def test_llm_summary_is_still_capped_at_three_sentences():
    verbose = "Раз. Два. Три. Четыре. Пять."
    assert summarize("вывод", llm=lambda _: verbose).sentences == 3
