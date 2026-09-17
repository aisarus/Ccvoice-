#!/usr/bin/env bash
# Мост в Telegram: один раз завести бота и узнать номер своего чата.
#
#   sudo bash /opt/voice-shell/scripts/setup-telegram.sh
#
# Что понадобится: токен бота от @BotFather. Номер чата скрипт узнает сам —
# просто напиши боту любое слово, когда он попросит.
set -euo pipefail

ENV_FILE="${VOICE_ENV_FILE:-/etc/voice-shell.env}"
API="https://api.telegram.org"

die() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

[ -w "$(dirname "$ENV_FILE")" ] || die "нужен root: запусти через sudo"

say "Бот"
cat <<'HOWTO'
1. Открой в телеграме @BotFather
2. /newbot — придумай имя и адрес бота
3. Он выдаст токен вида 1234567890:AAH...
HOWTO

TOKEN="${TELEGRAM_TOKEN:-}"
if [ -z "$TOKEN" ]; then
    read -r -p "Вставь токен бота: " TOKEN < /dev/tty || true
fi
# Из чата токен копируют вместе с лишним — чистим сами.
TOKEN="$(printf '%s' "$TOKEN" | tr -d '[:space:]' | sed 's/^TELEGRAM_TOKEN=//; s/^"//; s/"$//')"
printf '%s' "$TOKEN" | grep -qE '^[0-9]{6,}:[A-Za-z0-9_-]{30,}$' \
    || die "это не похоже на токен бота: он вида 1234567890:ABC…"

имя="$(curl -fsS --max-time 20 "$API/bot$TOKEN/getMe" \
        | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["username"])' 2>/dev/null)" \
    || die "телеграм не принял токен"
say "Бот @$имя на связи"

CHAT="${TELEGRAM_CHAT_ID:-}"
if [ -z "$CHAT" ]; then
    echo "Теперь напиши боту @$имя любое слово — жду до минуты."
    for _ in $(seq 1 30); do
        CHAT="$(curl -fsS --max-time 10 "$API/bot$TOKEN/getUpdates" \
            | python3 -c '
import sys, json
данные = json.load(sys.stdin).get("result", [])
чаты = [u["message"]["chat"]["id"] for u in данные if "message" in u]
print(чаты[-1] if чаты else "")' 2>/dev/null || true)"
        [ -n "$CHAT" ] && break
        sleep 2
    done
fi
[ -n "$CHAT" ] || die "сообщения от тебя не пришло — напиши боту и запусти снова"
say "Чат: $CHAT"

# Пишем в файл службы: секреты живут там, а не в репозитории.
umask 077
grep -v '^TELEGRAM_TOKEN=\|^TELEGRAM_CHAT_ID=' "$ENV_FILE" > "$ENV_FILE.new" 2>/dev/null || true
{
    cat "$ENV_FILE.new" 2>/dev/null || true
    echo "TELEGRAM_TOKEN=$TOKEN"
    echo "TELEGRAM_CHAT_ID=$CHAT"
} > "$ENV_FILE"
rm -f "$ENV_FILE.new"
chmod 600 "$ENV_FILE"

curl -fsS --max-time 20 -X POST "$API/bot$TOKEN/sendMessage" \
    -H 'Content-Type: application/json' \
    -d "{\"chat_id\":\"$CHAT\",\"text\":\"Мост поднят. Скажи голосом: «скинь мне в телегу».\"}" \
    >/dev/null || die "не смог отправить проверочное сообщение"

systemctl restart voice-shell 2>/dev/null || true
say "Готово — проверь телеграм, там сообщение от бота"
