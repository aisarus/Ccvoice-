"""Разбор вывода `claude setup-token`.

Терминал печатает ссылку с переносами, поэтому видимый текст обрезан на
середине. Целую ссылку несёт OSC 8 — гиперссылка. Обрезанная ссылка выглядит
правдоподобно, но claude.ai отвечает на неё «Invalid OAuth Request».
"""
import pytest

from voice_claude.auth import REQUIRED_PARAMS, extract_auth_url, strip_ansi

FULL = (
    "https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-"
    "5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth"
    "%2Fcode%2Fcallback&scope=user%3Ainference&code_challenge=pRE1WcbSWLtWjYWEK_x5LzPxXvc2"
    "l_h5T6-v8PHXNXc&code_challenge_method=S256&state=FoTpXX3PL-XqNI1oZf2aLqqmtI7zE_NgZx7kW6N4RaQ"
)
CHUNKS = [FULL[i:i + 78] for i in range(0, len(FULL), 78)]

# Как это реально выглядит в pty: каждая строка — кусок ссылки, обёрнутый
# в гиперссылку с полным адресом, плюс управляющие последовательности цвета.
WRAPPED_WITH_OSC8 = (
    "\x1b[38;5;174mWelcome\x1b[9Gto\x1b[12GClaude\x1b[19GCode\x1b[39m\r\n\r\n"
    "\x1b[2G\x1b[38;5;246mBrowser\x1b[10Gdidn't\x1b[17Gopen?\x1b[39m\r\n\r\n"
    + "".join(f"\x1b]8;id=8rdg6b;{FULL}\x1b\\\x1b[38;5;246m{chunk}\x1b[39m\x1b]8;;\x1b\\\r\n"
             for chunk in CHUNKS)
    + "\r\n\x1b[2GPaste\x1b[8Gcode\x1b[13Ghere\x1b[18Gif\x1b[21Gprompted\x1b[30G> "
)
WRAPPED_PLAIN = "Browser didn't open? Use the url below\r\n" + "\r\n".join(CHUNKS) + "\r\nPaste code here > "


def test_full_url_is_recovered_from_the_terminal_hyperlink():
    assert extract_auth_url(WRAPPED_WITH_OSC8) == FULL


def test_full_url_is_recovered_when_only_the_wrapped_text_is_present():
    assert extract_auth_url(WRAPPED_PLAIN) == FULL


def test_the_visible_first_line_alone_is_not_accepted():
    """Обрезанная ссылка выглядит правильной — и ломает авторизацию."""
    truncated = "Use the url below\r\n" + CHUNKS[0] + "\r\nPaste code here > "
    assert extract_auth_url(truncated) is None


@pytest.mark.parametrize("param", REQUIRED_PARAMS)
def test_every_required_parameter_survives(param):
    assert param in extract_auth_url(WRAPPED_WITH_OSC8)


def test_nothing_is_invented_when_there_is_no_url():
    assert extract_auth_url("Welcome to Claude Code\r\nSomething went wrong\r\n") is None


def test_ansi_stripping_leaves_the_prompt_readable():
    assert "Paste" in strip_ansi(WRAPPED_WITH_OSC8)


def test_failure_reports_what_the_cli_said():
    """Иначе «код не принят» — это гадание вместо диагноза."""
    from voice_claude.auth import SetupTokenFlow

    raw = ("Welcome to Claude Code\r\n"
           "Paste code here if prompted > abc\r\n"
           "\x1b[31mError: OAuth token exchange failed: authorization code expired\x1b[39m\r\n")
    detail = SetupTokenFlow._tail(raw)
    assert "authorization code expired" in detail
    assert "Welcome" not in detail


def test_a_broken_token_is_not_reported_as_a_working_subscription(monkeypatch):
    """«subscription» при мусоре в переменной — это ложный зелёный свет."""
    from voice_claude.auth import credential_kind, credential_problem, token_problem

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "Вставь сюда")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert credential_kind() == "none"
    assert "не-ASCII" in credential_problem()

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "T" * 40)
    assert credential_kind() == "subscription"
    assert credential_problem() is None


def test_token_problems_are_named():
    from voice_claude.auth import token_problem

    assert token_problem("") == "пусто"
    assert "короткий" in token_problem("sk-ant-1")
    assert "sk-ant-" in token_problem("x" * 40)
    assert token_problem("sk-ant-oat01-" + "T" * 40) is None


def test_token_is_persisted_into_the_environment_file(tmp_path):
    """Иначе подписку придётся подключать заново после каждого перезапуска."""
    from voice_claude.auth import persist_token

    env = tmp_path / "voice-shell.env"
    env.write_text("VOICE_TOKEN=abc\nCLAUDE_CODE_OAUTH_TOKEN=старое\nPORT=8790\n", encoding="utf-8")
    assert persist_token("sk-ant-oat01-" + "T" * 40, str(env))

    written = env.read_text(encoding="utf-8")
    assert written.count("CLAUDE_CODE_OAUTH_TOKEN=") == 1
    assert "sk-ant-oat01-" in written
    assert "VOICE_TOKEN=abc" in written and "PORT=8790" in written


def test_missing_environment_file_is_reported_not_created(tmp_path):
    from voice_claude.auth import persist_token

    assert not persist_token("sk-ant-oat01-" + "T" * 40, str(tmp_path / "нет-такого.env"))


def test_cli_login_counts_as_a_credential(tmp_path, monkeypatch):
    """Если CLI вошёл сам, переменная с токеном не нужна — и демон не должен врать «none»."""
    from voice_claude.auth import cli_authenticated, credential_kind

    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = tmp_path / "claude"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

    assert not cli_authenticated()
    assert credential_kind() == "none"

    (config / ".credentials.json").write_text('{"oauth": "..."}', encoding="utf-8")
    assert cli_authenticated()
    assert credential_kind() == "cli"


def test_github_access_is_detected_by_the_token(monkeypatch):
    from voice_claude.auth import github_ready

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert not github_ready()

    monkeypatch.setenv("GH_TOKEN", "не токен")
    assert not github_ready()

    monkeypatch.setenv("GH_TOKEN", "ghp_" + "x" * 36)
    assert github_ready()
