"""Очередь задач: сказал и забыл."""
import subprocess

import pytest

from voice_claude import tasks


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "auth.ts").write_text("было", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "первый")
    return tmp_path


def test_each_task_gets_its_own_copy_of_the_project(repo):
    """Две задачи в одной рабочей копии подрались бы за одни и те же файлы."""
    queue = tasks.TaskQueue(repo)
    first = queue.start(queue.add("почини падающий тест"))
    second = queue.start(queue.add("добавь поле таймаут в конфиг"))

    assert first.worktree != second.worktree
    assert first.branch != second.branch
    # Правка в одной копии не видна в другой и не трогает исходную.
    (repo / ".voice-shell" / "work" / first.id / "auth.ts").write_text("стало", encoding="utf-8")
    assert (repo / ".voice-shell" / "work" / second.id / "auth.ts").read_text() == "было"
    assert (repo / "auth.ts").read_text() == "было"


def test_more_than_two_at_once_is_not_started(repo):
    """Это чужая машина и чужие деньги: потолок нужен."""
    queue = tasks.TaskQueue(repo, parallel=2)
    for text in ("первая", "вторая", "третья"):
        queue.add(text)
    queue.start(queue.next_to_start())
    queue.start(queue.next_to_start())
    assert queue.next_to_start() is None, "третья пошла в работу сверх потолка"

    queue.finish(queue.running[0], "готово")
    assert queue.next_to_start() is not None


def test_a_restart_does_not_lose_what_was_said(repo):
    """Перезапуск службы не должен стирать сказанное человеком."""
    queue = tasks.TaskQueue(repo)
    queue.add("почини сборку")
    started = queue.start(queue.add("посмотри логи"))

    снова = tasks.TaskQueue(repo)
    assert [t.text for t in снова.tasks] == ["почини сборку", "посмотри логи"]
    # Задача, которая была в работе, после перезапуска не «в работе».
    assert снова.get(started.id).state == tasks.STUCK
    assert снова.get(started.id).summary


def test_a_task_is_found_by_words_not_by_number(repo):
    """Голосом номера не называют: «отмени задачу про тесты»."""
    queue = tasks.TaskQueue(repo)
    queue.add("почини падающие тесты в ветке auth")
    нужная = queue.add("обнови зависимости фронтенда")

    assert queue.find("отмени задачу про зависимости").id == нужная.id
    assert queue.find("про совершенно другое") is None


def test_the_status_is_one_spoken_sentence(repo):
    """В ухо идёт фраза, а не таблица."""
    queue = tasks.TaskQueue(repo)
    assert "Ничего не делаю" in queue.spoken_status()

    queue.start(queue.add("почини падающий тест"))
    queue.add("обнови зависимости")
    сказано = queue.spoken_status()
    assert "Делаю" in сказано and "в очереди 1" in сказано
    assert len(сказано) < 160


def test_finished_tasks_are_told_about_once(repo):
    """Дважды рассказать о сделанном — это уже болтун."""
    queue = tasks.TaskQueue(repo)
    task = queue.start(queue.add("почини тест"))
    queue.finish(task, "Готово, тесты зелёные.")

    ждут = queue.undeliverable()
    assert [t.id for t in ждут] == [task.id]
    queue.mark_delivered(ждут)
    assert queue.undeliverable() == []


def test_a_copy_can_be_removed_without_losing_the_branch(repo):
    """Копия — временная, работа — нет."""
    queue = tasks.TaskQueue(repo)
    task = queue.start(queue.add("почини тест"))
    ветка = task.branch
    queue.drop_worktree(task)

    assert not (repo / ".voice-shell" / "work" / task.id).exists()
    branches = subprocess.run(["git", "-C", str(repo), "branch", "--list", ветка],
                              capture_output=True, text=True).stdout
    assert ветка in branches


def test_a_task_becomes_background_because_the_person_said_so(repo):
    """Фоновой задачу делает не длительность, а решение не ждать ответа."""
    assert tasks.background_request("в фоне почини падающие тесты") == "почини падающие тесты"
    assert tasks.background_request("займись обновлением зависимостей") == "обновлением зависимостей"
    assert tasks.background_request("почини падающие тесты") is None


def test_questions_about_the_queue_are_recognised(repo):
    assert tasks.is_status_question("клод чем занят")
    assert tasks.is_status_question("что в очереди")
    assert tasks.is_ready_question("что готово")
    assert not tasks.is_status_question("что такое вектор эмбеддинга")


def test_cancelling_names_the_task_in_words(repo):
    """Номера задач голосом не называют."""
    assert tasks.cancel_request("отмени задачу про зависимости") == "зависимости"
    assert tasks.cancel_request("отмени последнее") is None


# -- как это выглядит через настоящий протокол --------------------------------

def test_a_background_task_is_taken_and_told_about(tmp_path, monkeypatch):
    """«В фоне почини тесты» — реплика уходит в очередь, а не в сессию."""
    import asyncio
    import json as js
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve
    from voice_claude.server import Daemon, Settings

    repo = tmp_path / "ws"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "auth.ts").write_text("было", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "первый")

    async def flow():
        daemon = Daemon(Settings(workspace=str(repo), token="t",
                                 note_path=str(tmp_path / "i.md")))
        heard = []
        async with serve(daemon.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(js.dumps({"id": "hello", "v": 1, "token": "t"}))
                await ws.recv()
                await ws.send(js.dumps({
                    "id": "speech_segment", "segment_id": "s1",
                    "transcript": "в фоне почини падающие тесты",
                    "device": "phone_mic", "duration_ms": 1400,
                    "voiced_frames": 45, "role": "master"}))
                try:
                    while True:
                        heard.append(js.loads(await asyncio.wait_for(ws.recv(), timeout=1.5)))
                except asyncio.TimeoutError:
                    pass
        return daemon, heard

    daemon, heard = asyncio.run(flow())
    сказано = [m.get("text", "") for m in heard if m.get("id") == "voice_summary"]
    assert any("Взял в работу" in t for t in сказано), сказано
    assert [t.text for t in daemon.queue.tasks] == ["почини падающие тесты"]
    # Реплика не ушла в обычную маршрутизацию: это задача, а не разговор.
    assert not [m for m in heard if m.get("id") == "route"]
