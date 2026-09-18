"""Скрипты: то, что bash проверяет только в момент запуска.

Один такой промах уже стоил человеку испорченной настройки: в bash имя
переменной обязано быть латиницей, а `имя=...` он молча принимает за команду
и падает на полпути — уже после того, как токен введён.
"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted(ROOT.glob("scripts/*.sh")) + [ROOT / "android" / "smoke-test.sh"]
# `arr+=(...)` — это дописать в массив, а не имя переменной с плюсом.
# Плюс снимается здесь, а не в списке исключений: иначе первое же дописывание
# в массив выглядело бы как кириллица в имени и роняло проверку на ровном месте.
ASSIGNMENT = re.compile(r"^\s*([^\s=()#]+?)\+?=[^=]", re.M)
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_the_script_parses(script):
    done = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_variable_names_are_latin(script):
    """«имя=Ccichekbot: command not found» — настоящая поломка у человека."""
    плохие = []
    for name in ASSIGNMENT.findall(script.read_text(encoding="utf-8")):
        if name.startswith(("$", '"', "'", "[", "-")) or "/" in name:
            continue
        if not NAME.fullmatch(name):
            плохие.append(name)
    assert not плохие, f"{script.name}: {плохие}"


# doctor.sh — отчёт: он обязан дойти до конца, даже когда половина сломана.
# Ему set -e противопоказан, остальным — необходим.
ДОКЛАДЧИКИ = {"doctor.sh"}


@pytest.mark.parametrize("script", [s for s in SCRIPTS if s.name not in ДОКЛАДЧИКИ],
                         ids=lambda p: p.name)
def test_the_script_stops_on_the_first_failure(script):
    """Без set -e установщик доходит до конца, оставив половину сделанной."""
    assert "set -e" in script.read_text(encoding="utf-8"), "нет set -e"


# -- две ловушки, которые молчат ------------------------------------------
#
# Обе стоили человеку половины настройки, доведённой до середины без единого слова
# об ошибке. Обе видны только в момент запуска, поэтому ловим их здесь.

def _substitutions(text):
    """Все $(...) с учётом вложенности."""
    out, i = [], 0
    while True:
        start = text.find("$(", i)
        if start < 0:
            return out
        depth, j = 0, start + 1
        while j < len(text):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append(text[start:j + 1])
        i = start + 2


@pytest.mark.parametrize("script", [s for s in SCRIPTS if s.name not in ДОКЛАДЧИКИ],
                         ids=lambda p: p.name)
def test_a_silenced_pipeline_cannot_kill_the_script(script):
    """`X="$(sed … 2>/dev/null | tail -1)"` под `set -e` и `pipefail` — это
    выход без единого слова, когда файла нет.

    Ровно так обновление на сервере останавливалось сразу после проверки
    спеки: не было `/etc/caddy/Caddyfile`, sed вернул двойку, pipefail
    превратил её в провал подстановки, `set -e` убил скрипт, а `2>/dev/null`
    — мой же — съел единственное сообщение. Человек увидел приглашение
    оболочки и решил, что всё прошло.
    """
    text = script.read_text(encoding="utf-8")
    if "pipefail" not in text:
        return
    # Обрыв строки обратной косой — это всё ещё одна команда, и `|| die`
    # после неё ловит провал подстановки не хуже, чем `|| true` внутри.
    опасные = []
    for строка in text.replace("\\\n", " ").splitlines():
        if "||" in строка:
            continue
        if any("2>/dev/null" in s and "|" in s for s in _substitutions(строка)):
            опасные.append(строка.strip())
    assert not опасные, f"{script.name}: провал некому поймать — {опасные}"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_a_script_that_rewrites_itself_runs_from_a_copy(script):
    """bash читает файл по мере выполнения, по смещению в байтах.

    `git reset --hard` по репозиторию, в котором лежит сам скрипт, меняет
    этот файл под ним: дальше выполнение продолжается с того же смещения, но
    уже в другом тексте — попадает в середину чужой строки или молча
    упирается в конец. Нулевой код возврата, половина работы. Лечится
    единственным способом: уйти в копию до первой правки.
    """
    text = script.read_text(encoding="utf-8")
    if "git reset --hard" not in text and "git -C \"$ROOT\" reset" not in text:
        return
    assert "VOICE_SHELL_SELF_COPY" in text, \
        f"{script.name}: переписывает свой репозиторий, но не уходит в копию"
