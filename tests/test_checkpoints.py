"""Точки отката: «Клод, откати последнее» должно работать всегда."""
import subprocess

import pytest

from voice_claude import checkpoints


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@test")
    git(tmp_path, "config", "user.name", "test")
    (tmp_path / "auth.ts").write_text("было", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "первый")
    return tmp_path


def test_a_plain_directory_is_not_a_repo(tmp_path):
    assert not checkpoints.is_repo(tmp_path)
    assert not checkpoints.is_repo(tmp_path / "нет-такого")


def test_changes_are_committed_and_can_be_undone(repo):
    journal = checkpoints.Journal(repo)
    before = checkpoints.head(repo)

    (repo / "auth.ts").write_text("стало", encoding="utf-8")
    after = checkpoints.commit_all(repo, "почини auth")
    assert after and after != before

    point = journal.add(before, after, "почини auth")
    assert journal.last() is point

    checkpoints.reset_to(repo, point.before)
    assert (repo / "auth.ts").read_text(encoding="utf-8") == "было"


def test_nothing_to_commit_makes_no_checkpoint(repo):
    assert checkpoints.commit_all(repo, "ничего") is None


def test_undone_work_is_not_lost(repo):
    """Откат возвращает состояние, но сам коммит остаётся в истории."""
    before = checkpoints.head(repo)
    (repo / "auth.ts").write_text("стало", encoding="utf-8")
    after = checkpoints.commit_all(repo, "правка")

    checkpoints.reset_to(repo, before)
    kept = subprocess.run(["git", "-C", str(repo), "cat-file", "-t", after],
                          capture_output=True, text=True)
    assert kept.stdout.strip() == "commit"


def test_summary_names_the_files(repo):
    before = checkpoints.head(repo)
    (repo / "auth.ts").write_text("стало", encoding="utf-8")
    (repo / "session.ts").write_text("новое", encoding="utf-8")
    after = checkpoints.commit_all(repo, "две правки")
    assert checkpoints.summary(repo, before, after) == "auth.ts и session.ts"


def test_journal_survives_a_restart(repo):
    first = checkpoints.Journal(repo)
    first.add("aaa", "bbb", "первая правка")
    second = checkpoints.Journal(repo)
    assert second.last().utterance == "первая правка"


def test_undo_phrases_are_recognised():
    for phrase in ("откати последнее", "отмени последнее", "верни как было", "Клод, откати"):
        assert checkpoints.matches(phrase, checkpoints.UNDO_PHRASES)
    assert not checkpoints.matches("почини тесты", checkpoints.UNDO_PHRASES)
    assert checkpoints.matches("что ты сделал", checkpoints.HISTORY_PHRASES)


def test_our_own_journal_never_lands_in_the_project_history(repo):
    """Живая проверка показала: журнал точек уезжал в коммит проекта."""
    journal = checkpoints.Journal(repo)
    (repo / "auth.ts").write_text("стало", encoding="utf-8")
    before = checkpoints.head(repo)
    after = checkpoints.commit_all(repo, "голосом: правка")
    journal.add(before, after, "правка")

    files = subprocess.run(["git", "-C", str(repo), "show", "--name-only", "--format=", after],
                           capture_output=True, text=True).stdout.split()
    assert files == ["auth.ts"], files
    assert not any(checkpoints.STATE_DIR in name for name in files)


def test_writing_the_journal_is_not_a_change_worth_committing(repo):
    """Иначе каждая запись журнала выглядела бы как правка в проекте."""
    checkpoints.is_repo(repo)
    journal = checkpoints.Journal(repo)
    journal.add("a" * 40, "b" * 40, "что-то")
    assert not checkpoints.has_changes(repo)
    assert checkpoints.commit_all(repo, "пустой") is None


def test_the_state_directory_is_hidden_locally_not_in_the_project(repo):
    """Прячем в .git/info/exclude: это личный список, он никуда не уезжает."""
    checkpoints.is_repo(repo)
    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert f"{checkpoints.STATE_DIR}/" in exclude
    assert not (repo / ".gitignore").exists()
