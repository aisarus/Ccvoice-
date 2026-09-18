"""Claude Code на сервере должен работать в полную силу — и знать, в какую.

Здесь держатся два обещания. Первое: сессия получает системный промпт Claude
Code, а не пустую строку, — это разница между «умеет доводить работу до
конца» и «отвечает вяло, хотя инструменты на месте», и снаружи она выглядит
не поломкой, а деградацией модели. Второе: приписка к промпту говорит правду
о машине. Обещать ffmpeg, которого нет, хуже, чем промолчать: сессия потратит
ход, упрётся в «command not found» и объяснит это человеку, который в этот
момент идёт по улице.
"""
import http
import json
import re
from pathlib import Path

import pytest

from voice_claude import capabilities, server

SERVER = {"PUBLIC_DIR": "/opt/voice-shell/public",
          "PUBLIC_URL": "https://voice.example/p"}


# -- что на машине есть ----------------------------------------------------

def test_publishing_needs_both_halves():
    """Каталог без адреса назвать нечем, адрес без каталога — пустое обещание."""
    assert not capabilities.detect("/w", {"PUBLIC_DIR": "/pub"}).can_publish
    assert not capabilities.detect("/w", {"PUBLIC_URL": "https://x/p"}).can_publish
    assert capabilities.detect("/w", SERVER).can_publish


def test_missing_mcp_config_is_not_offered():
    """Несуществующий файл в опциях SDK — это отказ подняться на старте."""
    caps = capabilities.detect("/w", {"MCP_CONFIG": "/нет/такого.json"})
    assert caps.mcp_config is None


def test_mcp_config_is_taken_when_it_exists(tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text("{}")
    caps = capabilities.detect("/w", {"MCP_CONFIG": str(config)})
    assert caps.mcp_config == config


def test_skills_default_to_all_and_can_be_narrowed():
    assert capabilities.detect("/w", {}).skills == "all"
    assert capabilities.detect("/w", {"CODE_SKILLS": "сайт, видео"}).skills == ["сайт", "видео"]


def test_model_and_effort_come_from_the_machine():
    caps = capabilities.detect("/w", {"CODE_MODEL": "claude-opus-5", "CODE_EFFORT": "xhigh"})
    assert (caps.model, caps.effort) == ("claude-opus-5", "xhigh")


# -- что об этом знает сессия ----------------------------------------------

def test_briefing_names_the_publishing_address():
    text = capabilities.detect("/w", SERVER).briefing()
    assert "https://voice.example/p" in text
    assert "/opt/voice-shell/public" in text


def test_briefing_admits_when_there_is_nowhere_to_publish():
    """Молчание здесь означало бы «сделай сайт», который некому показать."""
    text = capabilities.detect("/w", {}).briefing()
    assert "No public directory" in text


def test_briefing_never_promises_a_tool_that_is_not_installed():
    caps = capabilities.detect("/w", SERVER)
    text = caps.briefing()
    for key, names, description in capabilities._TOOLS:
        if key not in caps.found:
            first = description.split(" ")[0]
            assert first not in text, f"обещан {first}, которого на машине нет"


def test_briefing_says_plainly_that_it_cannot_conjure_a_photograph():
    """Заглушка вместо фотографии — худший из возможных ответов: человек
    узнает об этом последним, уже показав её кому-то."""
    text = capabilities.detect("/w", SERVER).briefing()
    assert "cannot conjure" in text


# -- опции, с которыми поднимается сессия ----------------------------------

@pytest.fixture()
def options():
    pytest.importorskip("claude_agent_sdk")
    from voice_claude.targets import CodeTarget
    return CodeTarget("/opt/voice-shell/workspace")._options()


def test_code_session_gets_the_claude_code_system_prompt(options):
    """Главное обещание файла.

    SDK по умолчанию передаёт CLI `--system-prompt ''`. Claude Code без
    собственного промпта — это Claude Code без инструкций.
    """
    assert options.system_prompt["type"] == "preset"
    assert options.system_prompt["preset"] == "claude_code"
    assert "This session is spoken" in options.system_prompt["append"]


def test_code_session_reads_everything_on_disk(options):
    """CLAUDE.md, настройки, навыки, субагенты, свои команды."""
    assert set(options.setting_sources) == {"user", "project", "local"}
    assert options.skills == "all"


def test_the_built_command_has_no_empty_system_prompt(options):
    """Проверка не на наших намерениях, а на том, что уедет в CLI."""
    transport = pytest.importorskip(
        "claude_agent_sdk._internal.transport.subprocess_cli")
    built = object.__new__(transport.SubprocessCLITransport)
    built._options, built._cli_path = options, "claude"
    built._prompt, built._is_streaming = "x", True
    try:
        cmd = built._build_command()
    except AttributeError:  # pragma: no cover - внутренности SDK поменялись
        pytest.skip("SubprocessCLITransport собирается иначе")
    assert "--system-prompt" not in cmd
    assert "--append-system-prompt" in cmd


# -- отдача сделанного -----------------------------------------------------

@pytest.fixture()
def published(tmp_path):
    (tmp_path / "сайт").mkdir()
    (tmp_path / "сайт" / "index.html").write_text("<h1>готово</h1>")
    (tmp_path / "сайт" / "style.css").write_text("body{}")
    return tmp_path


def test_published_site_opens(published):
    reply = server.published_response("/p/сайт/", published)
    assert reply.status_code == 200
    assert "готово" in reply.body.decode()


def test_directory_without_slash_redirects(published):
    """Без косой черты все относительные ссылки внутри страницы уезжают
    уровнем выше: сайт открывается без стилей и картинок."""
    reply = server.published_response("/p/сайт", published)
    assert reply.status_code == 301
    assert reply.headers["Location"] == "/p/%D1%81%D0%B0%D0%B9%D1%82/"


def test_listing_when_there_is_no_index(published):
    reply = server.published_response("/p/", published)
    assert reply.status_code == 200
    assert "сайт/" in reply.body.decode()


def test_nothing_escapes_the_public_directory(published, tmp_path):
    secret = tmp_path.parent / "секрет.txt"
    secret.write_text("токен")
    for path in ("/p/../секрет.txt", "/p/сайт/../../секрет.txt", "/p/%2e%2e/секрет.txt"):
        reply = server.published_response(path, published)
        assert reply.status_code == http.HTTPStatus.NOT_FOUND, path


def test_a_huge_file_is_refused_rather_than_read_into_memory(published, monkeypatch):
    """Полуторагигабайтное видео в памяти — это смерть голосовой оболочки."""
    monkeypatch.setattr(server, "MAX_PUBLISHED_BYTES", 8)
    (published / "видео.mp4").write_bytes(b"0" * 64)
    reply = server.published_response("/p/видео.mp4", published)
    assert reply.status_code == http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE


# -- набор навыков ---------------------------------------------------------
#
# Навык — это папка с SKILL.md, которую Claude Code подхватывает сам, по
# описанию, из живой речи. Поэтому цена ошибки здесь тихая: неверный
# заголовок не роняет ничего, навык просто не появляется в списке, и
# «сделай сайт» остаётся просьбой без готового порядка действий. Узнать об
# этом голосом нельзя, поэтому проверяем здесь.

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "workspace"
SKILLS = sorted((WORKSPACE / ".claude" / "skills").glob("*/SKILL.md"))
AGENTS = sorted((WORKSPACE / ".claude" / "agents").glob("*.md"))
RULES = sorted((WORKSPACE / ".claude" / "rules").glob("*.md"))
NAME = re.compile(r"[a-z][a-z0-9-]*")
EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def _frontmatter(path):
    text = path.read_text()
    assert text.startswith("---\n"), f"{path}: заголовок должен начинаться с первой строки"
    head, _, body = text[4:].partition("\n---\n")
    meta = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        assert sep, f"{path}: строка заголовка без двоеточия: {line!r}"
        meta[key.strip()] = value.strip()
    return meta, body


def test_there_is_a_set_of_skills():
    assert len(SKILLS) >= 5


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_skill_is_shaped_the_way_claude_code_expects(path):
    meta, body = _frontmatter(path)
    name = path.parent.name
    # Имя папки становится командой, поэтому кириллица и пробелы в нём —
    # навык, который не позвать.
    assert NAME.fullmatch(name), f"{name}: только строчная латиница и дефис"
    assert meta.get("name") == name, "имя в заголовке должно совпадать с папкой"
    assert meta.get("effort", "high") in EFFORTS, meta.get("effort")
    assert body.strip(), "навык без тела ничего не делает"


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_skill_describes_when_it_fires(path):
    """Описание — единственное, по чему модель решает включить навык.

    Человек говорит по-русски и не знает, что у навыка есть имя, поэтому в
    описании должны стоять живые фразы, а не название функции.
    """
    meta, _ = _frontmatter(path)
    description = meta.get("description", "")
    assert len(description) > 80, "слишком короткое, чтобы по нему сработать"
    assert "«" in description, "нет живых фраз, на которые навык включается"


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_skill_does_not_promise_what_the_shell_cannot_do(path):
    """Навык, обещающий сочинить фотографию, — это заглушка, выданная за
    фотографию, и человек узнает об этом последним."""
    body = _frontmatter(path)[1].lower()
    if "фотограф" in body:
        assert "не можешь" in body or "нет" in body, \
            "фотография упомянута без оговорки, что сочинить её нельзя"


# -- рабочая папка сервера -------------------------------------------------
#
# Claude Code читает её сам, молча. Опечатка в JSON не роняет ничего — она
# просто означает, что настроек нет, и узнать об этом голосом невозможно:
# сессия будет работать слабее, и всё.

def test_settings_are_valid_json_with_the_keys_that_matter():
    settings = json.loads((WORKSPACE / ".claude" / "settings.json").read_text())
    # Ultracode — единственный ключ, который включает оркестровку воркфлоу.
    assert settings["ultracode"] is True
    # Часовой кэш: человек говорит урывками, между репликами проходят
    # минуты, и на пятиминутном кэше каждая следующая реплика заново
    # оплачивает весь разговор и ждёт его пересчёта.
    assert settings["promptCacheTtl"] == "1h"
    assert settings["subagentPromptCacheTtl"] == "1h"


def test_settings_keep_secrets_out_of_reach():
    deny = json.loads((WORKSPACE / ".claude" / "settings.json").read_text())["permissions"]["deny"]
    for secret in ("/etc/voice-shell.env", "~/.ssh/**", "~/.claude/.credentials.json"):
        assert any(secret in rule for rule in deny), secret


def test_effort_is_not_pinned_anywhere_it_would_silence_ultracode():
    """Явный уровень усилия идёт первым в порядке разрешения и подменяет
    собой ultracode вместе с оркестровкой. Значит по умолчанию его нет."""
    settings = json.loads((WORKSPACE / ".claude" / "settings.json").read_text())
    assert "effortLevel" not in settings
    assert settings.get("env", {}).get("CLAUDE_CODE_EFFORT_LEVEL") is None
    assert capabilities.detect(WORKSPACE, {}).effort is None


def test_ultracode_is_seen_by_the_daemon():
    assert capabilities.detect(WORKSPACE, {}).ultracode is True


def test_a_broken_settings_file_does_not_take_the_shell_down(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{ сломано")
    assert capabilities.detect(tmp_path, {}).ultracode is False


def test_there_is_a_standby_model():
    """Перегруженная модель — это тишина в ухе, а не сообщение об ошибке."""
    assert capabilities.detect("/w", {}).fallback_model == "sonnet"
    assert capabilities.detect("/w", {"CODE_FALLBACK_MODEL": "haiku"}).fallback_model == "haiku"


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_subagent_file_is_shaped_the_way_claude_code_expects(path):
    """Файл без name или description Claude Code пропускает молча, записав
    причину в отладочный лог, которого никто не читает."""
    meta, body = _frontmatter(path)
    assert NAME.fullmatch(meta.get("name", "")), meta.get("name")
    assert ":" not in meta.get("name", ""), "двоеточие зарезервировано за плагинами"
    assert len(meta.get("description", "")) > 60, "по описанию решают, звать ли его"
    assert meta.get("effort", "high") in EFFORTS
    assert body.strip()


def test_there_are_rules_and_they_are_not_empty():
    """Правила переживают сжатие контекста, а сказанное голосом — нет."""
    assert len(RULES) >= 3
    for rule in RULES:
        assert len(rule.read_text().split()) > 40, rule
