#!/usr/bin/env bash
# Довезти настройку Claude Code до рабочей папки на сервере.
#
#   bash scripts/sync-workspace.sh /opt/voice-shell/workspace
#
# Рабочая папка лежит отдельно от репозитория: обновление кода само по себе
# её не трогает. А именно там Claude Code читает всё, что делает его сильнее,
# — настройки, правила, навыки, субагентов.
#
# Что здесь чьё:
#
#   .claude/   наше. Перезаписывается каждый раз целиком по файлам. Свои
#              навыки и субагенты, лежащие рядом, остаются: копируется
#              содержимое, а не папка.
#   CLAUDE.md  твой. Кладётся один раз как заготовка и больше не трогается
#              никогда — иначе обновление стирало бы то, что ты про себя
#              написал.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/workspace"
TARGET="${1:-${WORKSPACE_DIR:-/opt/voice-shell/workspace}}"

[ -d "$SOURCE" ] || { echo "нет $SOURCE — репозиторий неполный" >&2; exit 1; }
mkdir -p "$TARGET/.claude"
cp -r "$SOURCE/.claude/." "$TARGET/.claude/"

if [ -f "$TARGET/CLAUDE.md" ]; then
    kept="CLAUDE.md на месте, не трогаю"
else
    cp "$SOURCE/CLAUDE.md" "$TARGET/CLAUDE.md"
    kept="CLAUDE.md положен заготовкой — впиши туда про себя"
fi

printf 'рабочая папка: %s\n' "$TARGET"
printf '  навыков    : %s\n' "$(find "$TARGET/.claude/skills" -name SKILL.md 2>/dev/null | wc -l)"
printf '  субагентов : %s\n' "$(find "$TARGET/.claude/agents" -name '*.md' 2>/dev/null | wc -l)"
printf '  правил     : %s\n' "$(find "$TARGET/.claude/rules" -name '*.md' 2>/dev/null | wc -l)"
printf '  %s\n' "$kept"
