#!/usr/bin/env bash
# Voice Shell на своём сервере: ставит демон, поднимает systemd-службу,
# при наличии домена выдаёт HTTPS. Запускать по ssh, можно с телефона.
#
#   curl -fsSL <этот файл> | sudo bash
#   curl -fsSL <этот файл> | sudo DOMAIN=voice.example.com bash
#
# Переменные:
#   DOMAIN        домен, указывающий на этот сервер -> автоматический HTTPS через Caddy
#   VOICE_TOKEN   свой токен доступа (по умолчанию генерируется)
#   WORKSPACE     каталог, в котором будет работать Claude Code
#   PORT          порт демона (по умолчанию 8787)
set -euo pipefail

BRANCH="claude/voice-shell-claude-code-77wwh2"
ROOT="/opt/voice-shell"
ENV_FILE="/etc/voice-shell.env"
SERVICE="/etc/systemd/system/voice-shell.service"
PORT="${PORT:-8787}"
WORKSPACE="${WORKSPACE:-/opt/voice-shell/workspace}"
DOMAIN="${DOMAIN:-}"
# С доменом впереди стоит Caddy, поэтому наружу порт открывать незачем.
BIND_HOST="${HOST:-$([ -n "$DOMAIN" ] && echo 127.0.0.1 || echo 0.0.0.0)}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# Ставят обычно так: curl … | sudo bash. Тогда stdin — это сам скрипт, и
# спросить человека через него нельзя. Но живой терминал рядом есть, он
# доступен как /dev/tty, — оттуда и спрашиваем.
ask() {
    local prompt="$1" answer=""
    if [ -t 0 ]; then
        read -r -p "$prompt" answer || true
    elif [ -r /dev/tty ]; then
        printf '%s' "$prompt" > /dev/tty
        read -r answer < /dev/tty || true
    fi
    printf '%s' "$answer"
}
have_human() { [ -t 0 ] || [ -r /dev/tty ]; }

# Проверка без последствий: посмотреть, всё ли на месте, ничего не трогая.
if [ -n "${CHECK:-}" ]; then
    printf 'проверка окружения (ничего не меняю)\n'
    for tool in git curl python3; do
        command -v "$tool" >/dev/null && printf '  %-8s есть\n' "$tool" \
                                       || printf '  %-8s НЕТ\n' "$tool"
    done
    command -v node >/dev/null && printf '  %-8s %s\n' node "$(node -v)" \
                               || printf '  %-8s НЕТ (поставлю сам)\n' node
    command -v claude >/dev/null && printf '  %-8s есть\n' claude \
                                 || printf '  %-8s НЕТ (поставлю сам)\n' claude
    command -v systemctl >/dev/null && printf '  %-8s есть\n' systemd \
                                    || printf '  %-8s НЕТ — служба не поднимется\n' systemd
    printf '  порт     %s\n' "$PORT"
    printf '  проект   %s\n' "$WORKSPACE"
    have_human && printf '  подписку спрошу вживую\n' \
               || printf '  спросить подписку не у кого: задай CLAUDE_CODE_OAUTH_TOKEN\n'
    exit 0
fi

[ "$(id -u)" -eq 0 ] || die "нужен root: запусти через sudo"

say "Пакеты"
if command -v apt-get >/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq git curl ca-certificates python3 python3-venv python3-pip
    if ! command -v node >/dev/null || [ "$(node -v | tr -d 'v' | cut -d. -f1)" -lt 18 ]; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
        apt-get install -y -qq nodejs
    fi
elif command -v dnf >/dev/null; then
    dnf install -y -q git curl python3 python3-pip nodejs
else
    die "неизвестный пакетный менеджер: поставь git, python3-venv и node 18+ вручную"
fi

say "Claude Code CLI"
command -v claude >/dev/null || npm install -g @anthropic-ai/claude-code >/dev/null
claude --version || die "CLI не встал"

say "Репозиторий"
if [ -d "$ROOT/.git" ]; then
    # Не pull: на разошедшихся ветках он останавливается на «divergent
    # branches» и требует выбрать способ слияния — а слиять здесь нечего,
    # нужна ровно та версия, что в ветке. Прежнее состояние помечаем меткой.
    git -C "$ROOT" fetch --quiet origin "$BRANCH" \
        || die "не смог забрать ветку $BRANCH — проверь сеть"
    git -C "$ROOT" tag -f "before-install-$(date +%Y%m%d-%H%M%S)" HEAD >/dev/null 2>&1 || true
    git -C "$ROOT" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
    git -C "$ROOT" reset --hard --quiet "origin/$BRANCH"
else
    git clone --quiet https://github.com/aisarus/Ccvoice-.git "$ROOT"
    git -C "$ROOT" checkout --quiet "$BRANCH"
fi
mkdir -p "$WORKSPACE"

say "Зависимости"
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --quiet --upgrade pip
"$ROOT/.venv/bin/pip" install --quiet -r "$ROOT/daemon/requirements-dev.txt"

say "Самопроверка"
( cd "$ROOT" && .venv/bin/python -m pytest tests -q && .venv/bin/python scripts/validate_spec.py ) \
    || die "тесты не прошли — дальше идти нельзя"

say "Подписка Claude"
OAUTH="${CLAUDE_CODE_OAUTH_TOKEN:-}"
if [ -z "$OAUTH" ] && have_human; then
    echo "Сейчас откроется авторизация: скопируй ссылку, открой на телефоне, вставь код обратно."
    if [ -t 0 ]; then
        claude setup-token || true
    else
        claude setup-token < /dev/tty > /dev/tty 2>&1 || true
    fi
    while :; do
        OAUTH="$(ask 'Вставь выданный токен (начинается с sk-ant-), или Enter чтобы пропустить: ')"
        OAUTH="$(printf '%s' "${OAUTH:-}" | tr -d '[:space:]')"
        [ -z "$OAUTH" ] && break
        # Проверяем здесь, иначе служба молча рапортует о готовности с мусором.
        case "$OAUTH" in
            sk-ant-*) [ "${#OAUTH}" -ge 20 ] && break
                      echo "Слишком короткий — это не весь токен." ;;
            *) echo "Токен должен начинаться с sk-ant-. Вставлено: ${OAUTH:0:12}…" ;;
        esac
    done
fi

TOKEN="${VOICE_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')}"

say "Порт"
systemctl stop voice-shell 2>/dev/null || true
if command -v ss >/dev/null && ss -lntH "sport = :$PORT" | grep -q .; then
    echo "Порт $PORT занят:"
    ss -lptnH "sport = :$PORT" || true
    die "перезапусти с другим портом: curl ... | sudo PORT=8790 bash"
fi

say "Служба"
umask 077
cat > "$ENV_FILE" <<ENV
VOICE_TOKEN=$TOKEN
WORKSPACE_DIR=$WORKSPACE
NOTE_PATH=$WORKSPACE/inbox.md
PORT=$PORT
HOST=$BIND_HOST
VOICE_ENV_FILE=$ENV_FILE
PYTHONPATH=$ROOT/daemon
${OAUTH:+CLAUDE_CODE_OAUTH_TOKEN=$OAUTH}
ENV
chmod 600 "$ENV_FILE"

cat > "$SERVICE" <<UNIT
[Unit]
Description=Voice Shell daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$ENV_FILE
WorkingDirectory=$ROOT/daemon
ExecStart=$ROOT/.venv/bin/python -m voice_claude
Restart=always
RestartSec=3
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now voice-shell
sleep 2
systemctl is-active --quiet voice-shell || { journalctl -u voice-shell -n 30 --no-pager; die "служба не поднялась"; }
curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null || die "демон не отвечает на /healthz"

URL="http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT"
if [ -n "$DOMAIN" ]; then
    say "HTTPS для $DOMAIN"
    if ! command -v caddy >/dev/null; then
        apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https
        curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
            | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
            > /etc/apt/sources.list.d/caddy-stable.list
        apt-get update -qq && apt-get install -y -qq caddy
    fi
    cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN {
    reverse_proxy 127.0.0.1:$PORT
}
CADDY
    systemctl restart caddy
    URL="https://$DOMAIN"
fi

cat <<REPORT

================================================================
  Voice Shell поднят.

  адрес     : $URL
  привязка  : $BIND_HOST:$PORT
  токен     : $TOKEN
  проект    : $WORKSPACE
  служба    : systemctl status voice-shell
  логи      : journalctl -u voice-shell -f

  В приложении на телефоне введи адрес и токен.
  Браузерный клиент требует https — он есть только если задан DOMAIN.
$([ "$BIND_HOST" = "0.0.0.0" ] && [ -z "$DOMAIN" ] && echo "
  БЕЗ ДОМЕНА порт открыт наружу и трафик идёт по http: токен защитит от
  чужих, но разговор поедет открытым текстом. Поставь домен (DOMAIN=...)
  или Tailscale и закрой порт фаерволом.")$([ -z "$OAUTH" ] && echo "
  ВНИМАНИЕ: подписка Claude не подключена — цели «код» и «чат» будут
  отвечать заглушкой. Подключить: claude setup-token, затем вписать
  CLAUDE_CODE_OAUTH_TOKEN в $ENV_FILE и systemctl restart voice-shell")
================================================================

REPORT
