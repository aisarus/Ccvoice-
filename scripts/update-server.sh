#!/usr/bin/env bash
# Обновление сервера одной командой.
#
# `git pull` здесь не годится: рабочая копия на сервере — не место для правок,
# а при малейшем расхождении pull останавливается на «divergent branches» и
# требует выбрать способ слияния. Слиять нечего: нужна ровно та версия, что
# в ветке. Поэтому fetch + reset, но сначала вслух о том, что пропадёт.
set -euo pipefail

ROOT="${VOICE_SHELL_DIR:-/opt/voice-shell}"
BRANCH="${VOICE_SHELL_BRANCH:-claude/voice-shell-claude-code-77wwh2}"

cd "$ROOT"
echo "— было: $(git log --oneline -1)"
git fetch -q origin "$BRANCH"

local_only="$(git log --oneline "origin/$BRANCH..HEAD" || true)"
if [ -n "$local_only" ]; then
    echo "— локальные коммиты, которых нет в ветке (они пропадут):"
    echo "$local_only"
fi
dirty="$(git status --porcelain || true)"
if [ -n "$dirty" ]; then
    echo "— правки в рабочей копии (они пропадут):"
    echo "$dirty"
fi

git reset --hard -q "origin/$BRANCH"
echo "— стало: $(git log --oneline -1)"

systemctl restart voice-shell
sleep 2
systemctl is-active voice-shell >/dev/null 2>&1 \
    && echo "— служба: перезапущена" \
    || { echo "::служба не поднялась::"; journalctl -u voice-shell -n 20 --no-pager; exit 1; }

PORT_VALUE="$(grep -oP '(?<=^PORT=).*' /etc/voice-shell.env 2>/dev/null | tail -1)"
TOKEN_VALUE="$(grep -oP '(?<=^VOICE_TOKEN=).*' /etc/voice-shell.env 2>/dev/null | tail -1)"
echo "— что думает демон:"
curl -fsS --max-time 5 "http://127.0.0.1:${PORT_VALUE:-8787}/healthz?token=${TOKEN_VALUE}" \
    || echo "  не ответил — смотри doctor.sh"
