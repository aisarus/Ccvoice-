"""Мост в Telegram: «скинь мне в телегу»."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from voice_claude import telegram


class Подставной(BaseHTTPRequestHandler):
    """Настоящего Telegram здесь нет, но контракт вызова проверить можно."""

    принято: list = []
    ответ = {"ok": True}
    код = 200

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        Подставной.принято.append((self.path, self.headers.get("Content-Type", ""), body))
        payload = json.dumps(Подставной.ответ).encode()
        self.send_response(Подставной.код)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def сервер():
    Подставной.принято = []
    Подставной.ответ = {"ok": True}
    Подставной.код = 200
    httpd = HTTPServer(("127.0.0.1", 0), Подставной)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_a_file_goes_to_the_one_configured_chat(сервер, tmp_path):
    """Адресата голосом не задать: рядом могут говорить другие."""
    файл = tmp_path / "config.json"
    файл.write_text('{"port": 8790}', encoding="utf-8")
    мост = telegram.Bridge(token="1234567890:" + "A" * 35, chat_id="42", api=сервер)

    assert мост.send_document(файл, "config.json") is None
    путь, тип, тело = Подставной.принято[0]
    assert путь.endswith("/sendDocument")
    assert "multipart/form-data" in тип
    assert b'name="chat_id"' in тело and b"42" in тело
    assert b'filename="config.json"' in тело and b'{"port": 8790}' in тело


def test_secrets_never_leave_the_machine(tmp_path):
    """Сказать «скинь конфиг» легко, а вынести вместе с ним ключи — необратимо."""
    for имя in (".env", "id_rsa", "server.pem", "voice-shell.keystore",
                "my-token.txt", "credentials.json"):
        (tmp_path / имя).write_text("секрет", encoding="utf-8")
        причина = telegram.refuse_reason(tmp_path / имя, tmp_path)
        assert причина and "секрет" in причина, имя


def test_a_file_outside_the_project_is_not_shared(tmp_path):
    """«Скинь /etc/shadow» — не тот случай, когда надо слушаться."""
    чужой = tmp_path / "снаружи.txt"
    чужой.write_text("не твоё", encoding="utf-8")
    проект = tmp_path / "проект"
    проект.mkdir()
    assert telegram.refuse_reason(чужой, проект) == "файл вне рабочего каталога"


def test_a_bad_token_is_said_in_human_words(сервер, tmp_path):
    Подставной.код = 401
    Подставной.ответ = {"ok": False, "description": "Unauthorized"}
    мост = telegram.Bridge(token="1234567890:" + "A" * 35, chat_id="42", api=сервер)
    assert мост.send_text("привет") == "телеграм не принял токен бота"


def test_an_unknown_chat_tells_what_to_do(сервер):
    Подставной.код = 400
    Подставной.ответ = {"ok": False, "description": "Bad Request: chat not found"}
    мост = telegram.Bridge(token="1234567890:" + "A" * 35, chat_id="999", api=сервер)
    assert "напиши боту первым" in (мост.send_text("привет") or "")


def test_without_settings_it_says_so_instead_of_failing():
    assert telegram.Bridge(token="", chat_id="").send_text("привет") == "телеграм не настроен"


def test_the_phrase_carries_what_to_send():
    assert telegram.share_request("клод скинь мне в телегу конфиг") == "конфиг"
    assert telegram.share_request("отправь в телеграм") == ""
    assert telegram.share_request("покажи логи сервера") is None


def test_the_phrase_survives_how_people_actually_say_it():
    """Список точных фраз не сработал на живом человеке: он сказал иначе,
    ответил сам Claude, и это прозвучало как «не умею писать в телегу»."""
    пустые = ["скинь мне в телегу", "отправь в телеграм", "скинь в телеграмм",
              "клод скинь это мне в телеграмм", "кинь в тг"]
    for фраза in пустые:
        assert telegram.share_request(фраза) == "", фраза

    с_именем = {"скинь мне в телеграмм конфиг": "конфиг",
                "перешли в тг конфиг": "конфиг",
                "в телегу кинь конфиг": "конфиг",
                "пришли мне в телеграмме логи": "логи"}
    for фраза, ожидалось in с_именем.items():
        assert telegram.share_request(фраза) == ожидалось, фраза


def test_other_sending_is_not_telegram():
    """«Отправь письмо» — не про телеграм, и перехватывать это нельзя."""
    for фраза in ("отправь письмо игорю", "пришли отчёт завтра",
                  "покажи логи сервера", "скинь скорость до минимума"):
        assert telegram.share_request(фраза) is None, фраза


def test_a_token_pasted_with_junk_is_named_as_such(сервер):
    """Токен копируют из чата вместе с лишним. Это должно звучать понятно,
    а не «телеграм ответил непонятным»."""
    for плохой in ("плохой", "TELEGRAM_TOKEN=1234567890:AAA", "1234567890", " "):
        причина = telegram.Bridge(token=плохой, chat_id="42", api=сервер).send_text("привет")
        assert причина and ("токен" in причина or "не настроен" in причина), плохой
    assert Подставной.принято == [], "с негодным токеном наружу ходить незачем"


def test_the_voice_command_sends_the_named_file(сервер, tmp_path, monkeypatch):
    """Через настоящий протокол: «скинь мне в телегу конфиг» — и файл ушёл."""
    import asyncio
    import subprocess
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve
    from voice_claude.server import Daemon, Settings

    репо = tmp_path / "проект"
    репо.mkdir()
    subprocess.run(["git", "-C", str(репо), "init", "-q"], check=True)
    (репо / "config.json").write_text('{"port": 8790}', encoding="utf-8")

    monkeypatch.setenv("TELEGRAM_TOKEN", "1234567890:" + "A" * 35)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("TELEGRAM_API", сервер)

    async def flow():
        daemon = Daemon(Settings(workspace=str(репо), token="t",
                                 note_path=str(tmp_path / "i.md")))
        сказано = []
        async with serve(daemon.handler, "127.0.0.1", 0) as server:
            порт = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{порт}") as ws:
                await ws.send(json.dumps({"id": "hello", "v": 1, "token": "t"}))
                await ws.recv()
                await ws.send(json.dumps({
                    "id": "speech_segment", "segment_id": "s1",
                    "transcript": "скинь мне в телегу конфиг", "device": "phone_mic",
                    "duration_ms": 1400, "voiced_frames": 45, "role": "master"}))
                try:
                    while True:
                        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                        if m.get("id") == "voice_summary":
                            сказано.append(m["text"])
                except asyncio.TimeoutError:
                    pass
        return сказано

    сказано = asyncio.run(flow())
    assert any("Отправил" in t for t in сказано), сказано
    пути = [путь for путь, _, _ in Подставной.принято]
    assert any(p.endswith("/sendDocument") for p in пути), пути
    assert any(b"config.json" in тело for _, _, тело in Подставной.принято)


def test_a_spoken_name_finds_a_latin_file():
    """«Скинь конфиг» — файл называется config.json, и точного совпадения
    не будет никогда."""
    файлы = ["config.json", "main.py", "auth.ts", "notes.md", "readme.md"]
    assert telegram.best_match("конфиг", файлы) == "config.json"
    assert telegram.best_match("мейн", файлы) == "main.py"
    assert telegram.best_match("ридми", файлы) == "readme.md"
    assert telegram.best_match("config", файлы) == "config.json"
    # «Аус ти эс» — это auth.ts, но по звучанию так не выводится честно.
    assert telegram.best_match("аус", файлы) is None


def test_a_name_that_matches_nothing_sends_nothing():
    """Отправить наружу не тот файл хуже, чем не отправить ничего."""
    assert telegram.best_match("совершенно другое", ["config.json", "main.py"]) is None
    assert telegram.best_match("", ["config.json"]) is None


def test_two_files_with_the_same_skeleton_are_not_guessed():
    """Костяк «mn» подходит и main.py, и money.py. Молчим."""
    assert telegram.best_match("мейн", ["main.py", "money.py"]) is None


def _демон(tmp_path, monkeypatch, сервер):
    import subprocess
    from voice_claude.server import Daemon, Settings
    репо = tmp_path / "проект"
    репо.mkdir()
    subprocess.run(["git", "-C", str(репо), "init", "-q"], check=True)
    monkeypatch.setenv("TELEGRAM_TOKEN", "1234567890:" + "A" * 35)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("TELEGRAM_API", сервер)
    return Daemon(Settings(workspace=str(репо), token="t",
                           note_path=str(tmp_path / "i.md"))), репо


def test_it_sends_the_thing_we_just_made(tmp_path, monkeypatch, сервер):
    """«Скинь мне ЕГО в телегу» — это не имя файла, а змейка, которую он
    только что написал. Живая поломка: он искал файл «его» и отвечал
    «такого файла нет»."""
    daemon, репо = _демон(tmp_path, monkeypatch, сервер)
    (репо / "snake.py").write_text("игра", encoding="utf-8")

    файлы = daemon._files_to_share("его", репо)
    assert [p.name for p in файлы] == ["snake.py"]


def test_a_deleted_file_is_never_offered(tmp_path, monkeypatch, сервер):
    """В diff попадают и удалённые файлы — отправлять их нечем."""
    daemon, репо = _демон(tmp_path, monkeypatch, сервер)
    (репо / "живой.txt").write_text("тут", encoding="utf-8")

    daemon.journal.add("a" * 40, "b" * 40, "правка голосом")
    monkeypatch.setattr("voice_claude.checkpoints.is_repo", lambda *a: True)
    monkeypatch.setattr("voice_claude.checkpoints.changed_files",
                        lambda *a: ["удалённый.txt", "живой.txt"])

    файлы = daemon._recently_changed(репо)
    assert [p.name for p in файлы] == ["живой.txt"]
