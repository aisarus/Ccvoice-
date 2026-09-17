#!/usr/bin/env bash
# Один отчёт обо всём состоянии: что за код, жива ли служба, что с токенами,
# отвечает ли демон. Запускать на сервере, вывод показывать целиком.
ROOT="${VOICE_SHELL_DIR:-/opt/voice-shell}"
ENV_FILE="${VOICE_ENV_FILE:-/etc/voice-shell.env}"

line() { printf '\n— %s\n' "$*"; }

line "код"
git -C "$ROOT" log --oneline -1 2>/dev/null || echo "нет репозитория в $ROOT"

line "служба"
systemctl is-active voice-shell 2>/dev/null || echo "не установлена"
systemctl is-enabled voice-shell 2>/dev/null || true

line "настройки"
if [ -r "$ENV_FILE" ]; then
    PORT_VALUE="$(grep -oP '(?<=^PORT=).*' "$ENV_FILE" | tail -1)"
    PORT_VALUE="${PORT_VALUE:-8787}"
    echo "порт: $PORT_VALUE"
    echo "рабочий каталог: $(grep -oP '(?<=^WORKSPACE_DIR=).*' "$ENV_FILE" | tail -1)"
    for name in VOICE_TOKEN CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY; do
        value="$(grep -oP "(?<=^$name=).*" "$ENV_FILE" | tail -1)"
        if [ -z "$value" ]; then
            echo "$name: не задан"
        elif printf '%s' "$value" | LC_ALL=C grep -q '[^ -~]'; then
            echo "$name: ПОРТИТСЯ — есть не-ASCII символы (${#value} симв.)"
        else
            echo "$name: ok, ${#value} симв., начинается с ${value:0:9}…"
        fi
    done
else
    echo "нет файла $ENV_FILE"
fi

line "вход CLI"
CRED="${CLAUDE_CONFIG_DIR:-/root/.claude}/.credentials.json"
if [ -s "$CRED" ]; then
    echo "CLI авторизован сам ($CRED) — переменная с токеном не обязательна"
else
    echo "файла входа нет ($CRED) — это ещё не отказ: демон спрашивает сам CLI"
fi
echo -n "прямая проверка CLI: "
timeout 90 env -u CLAUDE_CODE_OAUTH_TOKEN claude -p "ответь одним словом: работает" 2>&1 | head -3

line "порт"
ss -lntp 2>/dev/null | grep -E ":${PORT_VALUE:-8787}\b" || echo "никто не слушает ${PORT_VALUE:-8787}"

line "что думает сам демон"
TOKEN_VALUE="$(grep -oP '(?<=^VOICE_TOKEN=).*' "$ENV_FILE" 2>/dev/null | tail -1)"
curl -fsS --max-time 5 "http://127.0.0.1:${PORT_VALUE:-8787}/healthz?token=${TOKEN_VALUE}" \
    || echo "healthz не отвечает"

line "последние логи"
journalctl -u voice-shell -n 12 --no-pager 2>/dev/null | tail -12 || echo "логов нет"

line "сквозная проверка"
if [ -f "$ROOT/scripts/say.py" ]; then
    "$ROOT/.venv/bin/python" "$ROOT/scripts/say.py" "скажи привет"
else
    echo "нет $ROOT/scripts/say.py — сделай git pull"
fi
