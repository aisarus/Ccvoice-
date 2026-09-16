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
