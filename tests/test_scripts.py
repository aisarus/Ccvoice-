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
ASSIGNMENT = re.compile(r"^\s*([^\s=()#]+)=[^=]", re.M)
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
