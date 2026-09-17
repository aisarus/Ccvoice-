#!/usr/bin/env bash
# Сервер сам подтягивает ветку и перезапускается — но только если новая
# версия прошла проверку.
#
# Установить расписание (раз в десять минут):
#   sudo bash /opt/voice-shell/scripts/auto-update.sh --install-timer
# Выключить:
#   sudo systemctl disable --now voice-shell-update.timer
# Разово:
#   sudo bash /opt/voice-shell/scripts/auto-update.sh
set -euo pipefail

ROOT="${VOICE_SHELL_DIR:-/opt/voice-shell}"
BRANCH="${VOICE_SHELL_BRANCH:-claude/voice-shell-claude-code-77wwh2}"
UNIT="/etc/systemd/system/voice-shell-update.service"
TIMER="/etc/systemd/system/voice-shell-update.timer"
EVERY="${EVERY:-10min}"

log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }

if [ "${1:-}" = "--install-timer" ]; then
    [ "$(id -u)" -eq 0 ] || { echo "нужен root"; exit 1; }
    cat > "$UNIT" <<UNITEOF
[Unit]
Description=Voice Shell: подтянуть свежую версию
After=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $ROOT/scripts/auto-update.sh
UNITEOF
    cat > "$TIMER" <<TIMEREOF
[Unit]
Description=Voice Shell: проверять обновления

[Timer]
OnBootSec=2min
OnUnitActiveSec=$EVERY
Unit=voice-shell-update.service

[Install]
WantedBy=timers.target
TIMEREOF
    systemctl daemon-reload
    systemctl enable --now voice-shell-update.timer
    echo "расписание включено: проверка каждые $EVERY"
    systemctl list-timers voice-shell-update.timer --no-pager | head -3
    exit 0
fi

cd "$ROOT"
before="$(git rev-parse HEAD)"
git fetch -q origin "$BRANCH" || { log "нет сети — попробую в следующий раз"; exit 0; }
after="$(git rev-parse "origin/$BRANCH")"

[ "$before" = "$after" ] && exit 0

log "новая версия: $(git log --oneline -1 "origin/$BRANCH")"
git reset --hard -q "$after"

# Зависимости могли измениться вместе с кодом.
if ! git diff --quiet "$before" "$after" -- daemon/requirements-dev.txt daemon/requirements.txt; then
    log "ставлю зависимости"
    "$ROOT/.venv/bin/pip" install -q -r "$ROOT/daemon/requirements-dev.txt" || true
fi

# Проверяем ДО перезапуска: битую версию лучше не поднимать вовсе.
if ! ( "$ROOT/.venv/bin/python" -m pytest tests -q >/tmp/voice-update.log 2>&1 \
       && "$ROOT/.venv/bin/python" scripts/validate_spec.py >>/tmp/voice-update.log 2>&1 ); then
    log "проверка не прошла — возвращаюсь на прежнюю версию"
    tail -20 /tmp/voice-update.log
    git reset --hard -q "$before"
    exit 1
fi

systemctl restart voice-shell
sleep 2
if systemctl is-active --quiet voice-shell; then
    log "обновился до $(git log --oneline -1)"
else
    log "служба не поднялась — возвращаюсь на прежнюю версию"
    git reset --hard -q "$before"
    systemctl restart voice-shell
    exit 1
fi
