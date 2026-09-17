"""Две реплики сразу: одна сессия, одна рабочая копия, одна машина состояний.

Сообщения обрабатываются отдельными задачами, поэтому реплики идут внахлёст —
а Claude-сессия, git-репозиторий и состояние на всех одни. Живой прогон показал,
чем это кончается: точка отката указывала не туда, телефон гасил индикатор
работы посреди работы, а «работаю» звучало хором.
"""
import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from voice_claude.server import Daemon, Settings
from voice_claude.targets import Reply

MASTER = {"level_rel_db": 1.0, "snr_db": 30.0, "drr_db": 10.0, "c50_db": 14.0,
          "hf_ratio_db": 1.0, "lf_proximity_db": 4.0}


def segment(text, **over):
    payload = {"id": "speech_segment", "segment_id": "s", "transcript": text,
               "device": "phone_mic", "duration_ms": 1400, "voiced_frames": 45,
               "features": MASTER}
    payload.update(over)
    return payload


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    git(workspace, "init", "-q")
    git(workspace, "config", "user.email", "t@t")
    git(workspace, "config", "user.name", "t")
    (workspace / "README").write_text("проект", encoding="utf-8")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-qm", "первый")
    return workspace


class SlowCode:
    """Цель, которая правда меняет рабочую копию и делает это не мгновенно.

    Без настоящей правки точку отката проверять не на чем, а без задержки
    реплики не успевают наложиться друг на друга.
    """

    target_id = "code"
    available = True

    def __init__(self, workspace, delay=0.15):
        self.workspace = Path(workspace)
        self.delay = delay
        self.resets = 0
        self.interrupts = 0
        self.started = asyncio.Event()
        self.release = None

    async def send(self, text, preamble="", role_line=""):
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        else:
            await asyncio.sleep(self.delay)
        name = text.strip().split()[-1]
        (self.workspace / name).write_text(text, encoding="utf-8")
        return Reply(text=f"создал {name}", full_output=name, target="code")

    async def interrupt(self):
        self.interrupts += 1
        if self.release is not None:
            self.release.set()

    async def reset(self):
        self.resets += 1


async def talk(daemon, send_all, collect_s=3.0):
    """Поговорить с демоном по настоящему сокету и собрать всё сказанное."""
    heard = []
    async with serve(daemon.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"id": "hello", "v": 1, "token": "t"}))
            heard.append(json.loads(await ws.recv()))
            await send_all(ws)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + collect_s
            while loop.time() < deadline:
                try:
                    heard.append(json.loads(
                        await asyncio.wait_for(ws.recv(), timeout=deadline - loop.time())))
                except (asyncio.TimeoutError, TimeoutError):
                    break
    return heard


def make(workspace, tmp_path, **over):
    settings = Settings(workspace=str(workspace), port=0, token="t",
                        note_path=str(tmp_path / "inbox.md"), **over)
    return Daemon(settings)


def kinds(heard, kind):
    return [m for m in heard if m.get("id") == kind]


def answers(heard):
    return [m for m in kinds(heard, "voice_summary") if not m.get("progress")]


def test_two_utterances_in_a_row_get_their_own_rollback_points(repo, tmp_path):
    """Живой прогон: две реплики подряд запоминали один и тот же HEAD.

    Первая закоммитывала правки обеих, вторая не находила что коммитить, и
    «откати последнее» снимало заодно чужую работу.
    """
    daemon = make(repo, tmp_path, first_ack_s=5.0, progress_gap_s=5.0)
    daemon.targets.code = SlowCode(repo)
    daemon.targets._by_id["code"] = daemon.targets.code

    async def both(ws):
        await ws.send(json.dumps(segment("в код сделай alpha.txt")))
        await ws.send(json.dumps(segment("в код сделай beta.txt")))

    asyncio.run(talk(daemon, both))

    points = daemon.journal.recent(5)
    assert len(points) == 2, "не у каждой реплики своя точка отката"
    older, newer = points[1], points[0]
    assert older.after == newer.before, "вторая точка смотрит мимо первой"
    for point, name in ((older, "alpha.txt"), (newer, "beta.txt")):
        files = subprocess.run(
            ["git", "-C", str(repo), "show", "--name-only", "--format=", point.after],
            capture_output=True, text=True).stdout.split()
        assert files == [name], f"в коммите {point.title!r} оказалось {files}"


def test_the_shell_keeps_saying_it_works_while_another_reply_runs(repo, tmp_path):
    """Быстрый ответ гасил индикатор работы, пока длинная реплика ещё шла:
    телефон решал, что всё закончилось, и переставал ждать."""
    daemon = make(repo, tmp_path, first_ack_s=5.0, progress_gap_s=5.0)
    daemon.targets.code = SlowCode(repo, delay=1.0)
    daemon.targets._by_id["code"] = daemon.targets.code

    async def slow_then_quick(ws):
        await ws.send(json.dumps(segment("в код сделай gamma.txt")))
        await asyncio.sleep(0.1)
        await ws.send(json.dumps(segment("запиши мысль про второе ухо")))

    heard = asyncio.run(talk(daemon, slow_then_quick, collect_s=0.6))

    assert [m["text"] for m in answers(heard)] == ["Записал."], "длинная реплика уже ответила"
    assert kinds(heard, "state")[-1]["state"] != "SPEAKING", \
        "демон сказал, что всё сказано, пока работа идёт"


def test_only_one_reply_says_it_is_working(repo, tmp_path):
    """Две реплики начинали работу одновременно и говорили «работаю» хором."""
    daemon = make(repo, tmp_path, first_ack_s=0.05, progress_gap_s=5.0)
    daemon.targets.code = SlowCode(repo, delay=0.4)
    daemon.targets._by_id["code"] = daemon.targets.code

    async def both(ws):
        await ws.send(json.dumps(segment("в код сделай delta.txt")))
        await ws.send(json.dumps(segment("в код сделай epsilon.txt")))

    heard = asyncio.run(talk(daemon, both, collect_s=0.3))
    working = [m for m in kinds(heard, "voice_summary") if m.get("progress")]
    assert len(working) == 1, f"«работаю» прозвучало {len(working)} раза"


def test_undo_stops_the_work_before_resetting_the_copy(repo, tmp_path):
    """`git reset --hard` посреди работы — откат под руками у Claude:
    часть правок уже на диске, часть ещё нет, и вернётся мешанина."""
    daemon = make(repo, tmp_path, first_ack_s=5.0, progress_gap_s=5.0)
    code = SlowCode(repo)
    daemon.targets.code = code
    daemon.targets._by_id["code"] = daemon.targets.code
    # Настоящая прошлая правка: откатывать надо что-то, а не пустоту.
    before = _head(repo)
    (repo / "README").write_text("прошлая правка", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "голосом: прошлая правка")
    daemon.journal.add(before, _head(repo), "прошлая правка")

    async def work_then_undo(ws):
        code.release = asyncio.Event()
        await ws.send(json.dumps(segment("в код сделай zeta.txt")))
        await asyncio.wait_for(code.started.wait(), timeout=5)
        await ws.send(json.dumps(segment("откати последнее")))

    heard = asyncio.run(talk(daemon, work_then_undo, collect_s=2.0))

    assert code.interrupts == 1, "откат не остановил работу"
    spoken = " ".join(m["text"] for m in answers(heard))
    assert "Откатил README" in spoken, f"откат не состоялся: {spoken!r}"
    assert (repo / "README").read_text(encoding="utf-8") == "проект"
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip() == "", \
        "после отката в копии осталась половина работы"


def _head(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def test_a_broken_target_does_not_take_the_long_session_with_it(repo, tmp_path):
    """Общий сброс ронял долгую Claude Code-сессию из-за того, что не записался
    инбокс: работа начиналась с чистого листа не по своей вине."""
    daemon = make(repo, tmp_path, first_ack_s=5.0, progress_gap_s=5.0)
    daemon.targets.code = SlowCode(repo)
    daemon.targets._by_id["code"] = daemon.targets.code

    class BrokenNote:
        target_id = "note"
        available = True

        async def send(self, text, preamble="", role_line=""):
            raise OSError(28, "No space left on device")

    daemon.targets.note = BrokenNote()
    daemon.targets._by_id["note"] = daemon.targets.note

    async def one(ws):
        await ws.send(json.dumps(segment("запиши мысль про диск")))

    heard = asyncio.run(talk(daemon, one, collect_s=1.0))

    assert daemon.targets.code.resets == 0, "сломался инбокс, а пересоздали сессию Claude"
    spoken = " ".join(m["text"] for m in answers(heard))
    assert "не смог выполнить" in spoken, f"о поломке не сказали: {spoken!r}"


def test_a_workspace_without_git_warns_about_undo_once(tmp_path):
    """Человек должен знать, что откатывать будет нечем, — и узнать один раз,
    а не после каждой правки."""
    workspace = tmp_path / "no-git"
    workspace.mkdir()
    daemon = make(workspace, tmp_path, first_ack_s=5.0, progress_gap_s=5.0)
    daemon.targets.code = SlowCode(workspace, delay=0.0)
    daemon.targets._by_id["code"] = daemon.targets.code

    async def twice(ws):
        await ws.send(json.dumps(segment("в код сделай one.txt")))
        await asyncio.sleep(0.4)
        await ws.send(json.dumps(segment("в код сделай two.txt")))

    heard = asyncio.run(talk(daemon, twice, collect_s=0.8))
    warnings = [m for m in answers(heard) if "Точку отката" in m["text"]]
    assert len(warnings) == 1, f"предупреждение прозвучало {len(warnings)} раз"


def test_an_unexpected_failure_becomes_a_phrase_not_silence(repo, tmp_path):
    """Реплика живёт в своей задаче, и необработанное исключение не видно
    никак: связь цела, а ответа нет и не будет. Телефон просто глохнет."""
    daemon = make(repo, tmp_path, first_ack_s=5.0, progress_gap_s=5.0)

    async def nonsense(ws):
        await ws.send(json.dumps({"id": "ambient_control", "submode": "нет такого"}))

    heard = asyncio.run(talk(daemon, nonsense, collect_s=1.0))

    assert answers(heard), "о поломке не сказали ни слова"
    assert "Сбой" in answers(heard)[0]["text"]
    assert kinds(heard, "error")[0]["recoverable"] is True


def test_a_garbled_segment_is_answered_instead_of_swallowed(repo, tmp_path):
    """Телефон прислал мусор в признаках — раньше это было тихой смертью
    задачи: ни ответа, ни ошибки, ни разрыва связи."""
    daemon = make(repo, tmp_path, first_ack_s=5.0, progress_gap_s=5.0)

    async def garbled(ws):
        await ws.send(json.dumps(segment("посмотри файлы",
                                         features={"level_rel_db": "громко"})))

    heard = asyncio.run(talk(daemon, garbled, collect_s=1.0))
    assert answers(heard), "о поломке не сказали ни слова"
    assert kinds(heard, "error")[0]["code"] == "internal"


def test_a_handoff_carries_the_previous_answer_along(repo, tmp_path):
    """Сверка с документацией: `Router.handoff()` был написан и покрыт
    тестами, но сервер его не звал. «Объясни попроще» уходило в чат без
    единого слова о том, что объяснять."""
    daemon = make(repo, tmp_path, first_ack_s=5.0, progress_gap_s=5.0)
    daemon.targets.code = SlowCode(repo, delay=0.0)
    daemon.targets._by_id["code"] = daemon.targets.code

    seen = []

    class EchoChat:
        target_id = "chat"
        available = True

        async def send(self, text, preamble="", role_line=""):
            seen.append(text)
            return Reply(text="Объясняю.", full_output="Объясняю.", target="chat")

        async def reset(self):
            return None

    daemon.targets.chat = EchoChat()
    daemon.targets._by_id["chat"] = daemon.targets.chat

    async def work_then_ask(ws):
        await ws.send(json.dumps(segment("в код сделай theta.txt")))
        await asyncio.sleep(0.3)
        await ws.send(json.dumps(segment("объясни попроще")))

    heard = asyncio.run(talk(daemon, work_then_ask, collect_s=0.6))

    route = [m for m in kinds(heard, "route") if m.get("reason") == "handoff"]
    assert route and route[0]["target"] == "chat", "передача не состоялась"
    assert seen, "чат не получил ни одной реплики"
    assert "theta.txt" in seen[0], f"вывод code не доехал: {seen[0]!r}"
    assert "объясни попроще" in seen[0]


def test_a_handoff_without_anything_to_carry_says_so(repo, tmp_path):
    """Передавать нечего — это ответ, а не молчание и не пустой запрос."""
    daemon = make(repo, tmp_path, first_ack_s=5.0, progress_gap_s=5.0)

    async def ask(ws):
        await ws.send(json.dumps(segment("объясни попроще")))

    heard = asyncio.run(talk(daemon, ask, collect_s=0.5))
    assert answers(heard)[0]["text"] == "Пока нечего перекидывать."


def test_the_daemon_refuses_to_work_inside_its_own_checkout(tmp_path):
    """Наведённый на свою копию демон закоммитил незаконченную работу от лица
    «Voice Shell» и откатил её по первому «откати последнее»."""
    from voice_claude.server import bootstrap_workspace

    own = tmp_path / "voice-shell"
    (own / "daemon" / "voice_claude").mkdir(parents=True)
    (own / "daemon" / "voice_claude" / "server.py").write_text("...", encoding="utf-8")

    with pytest.raises(SystemExit) as refused:
        asyncio.run(bootstrap_workspace(Settings(workspace=str(own), token="t",
                                                 note_path=str(tmp_path / "i.md"))))
    assert "voice-shell" in str(refused.value)

    # Чужой проект по-прежнему годится.
    other = tmp_path / "project"
    other.mkdir()
    asyncio.run(bootstrap_workspace(Settings(workspace=str(other), token="t",
                                             note_path=str(tmp_path / "i.md"))))


def test_the_human_words_are_separated_from_the_service_lines(monkeypatch):
    """Живой прогон: Claude ответил «похоже, это системное сообщение об
    окружении, а не задача от вас» — реплика прилипала к метаданным, потому
    что пустую строку между ними отфильтровывало вместе с пустыми полями."""
    from voice_claude.spec import section
    from voice_claude.targets import CodeTarget

    example = section("speaker_identification")["claude_injection"]["example"]
    head, transcript = example["sent_to_claude"].rsplit("\n\n", 1)
    preamble, role_line = head.rsplit("\n", 1)
    assert transcript == example["input_transcript"], "транскрипт не должен меняться"

    sent = []

    class FakeClient:
        async def query(self, message):
            sent.append(message)

        async def receive_response(self):
            return
            yield  # pragma: no cover - генератор без единого элемента

    target = CodeTarget("/tmp")
    monkeypatch.setattr(CodeTarget, "available", property(lambda self: True))
    target._client = FakeClient()
    asyncio.run(target.send(transcript, preamble, role_line))

    assert sent == [example["sent_to_claude"]]
