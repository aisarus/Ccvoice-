#!/usr/bin/env bash
# Обновление сервера одной командой.
#
#   sudo bash /opt/voice-shell/scripts/update-server.sh
#
# `git pull` здесь не годится: рабочая копия на сервере — не место для правок,
# а при малейшем расхождении pull останавливается на «divergent branches» и
# требует выбрать способ слияния. Слиять нечего: нужна ровно та версия, что
# в ветке. Поэтому fetch + reset, но сначала вслух о том, что пропадёт, и с
# меткой, по которой прежнее состояние можно достать обратно.
#
# Переменные:
#   VOICE_SHELL_DIR     рабочая копия (по умолчанию /opt/voice-shell)
#   VOICE_SHELL_BRANCH  ветка (по умолчанию рабочая ветка проекта)
#   VOICE_ENV_FILE      файл окружения службы (по умолчанию /etc/voice-shell.env)
set -euo pipefail

ROOT="${VOICE_SHELL_DIR:-/opt/voice-shell}"
BRANCH="${VOICE_SHELL_BRANCH:-claude/voice-shell-claude-code-77wwh2}"
ENV_FILE="${VOICE_ENV_FILE:-/etc/voice-shell.env}"

die() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1 \
    || die "нет рабочей копии в $ROOT — сначала install-server.sh
(если копия лежит в другом месте: VOICE_SHELL_DIR=/путь sudo -E bash $0)"
# Проверяем права до сброса, а не после: иначе код бы обновился, а служба
# осталась на старом, и человек не понял бы, что произошло.
[ "$(id -u)" -eq 0 ] || die "нужен root — перезапуск службы без него не выйдет: sudo bash $0"
cd "$ROOT"

echo "— было: $(git log --oneline -1)"
git fetch -q origin "$BRANCH" \
    || die "не смог забрать ветку $BRANCH с origin.
Проверь сеть и доступ: git -C $ROOT fetch origin $BRANCH"

# Метка перед сбросом: локальные коммиты и текущее состояние остаются
# достижимы, даже если сейчас их сотрёт. Без неё «оно всё стёрло» — правда.
STAMP="before-update-$(date +%Y%m%d-%H%M%S)"
git tag -f "$STAMP" HEAD >/dev/null 2>&1 || true
# Меток копится по одной на обновление — держим последние десять.
git tag -l 'before-update-*' | sort | head -n -10 | xargs -r git tag -d >/dev/null 2>&1 || true

local_only="$(git log --oneline "origin/$BRANCH..HEAD" 2>/dev/null || true)"
if [ -n "$local_only" ]; then
    echo "— локальные коммиты, которых нет в ветке (они уйдут из HEAD):"
    echo "$local_only"
    echo "  вернуть: git -C $ROOT reset --hard $STAMP"
fi
# Разделяем нарочно: reset --hard стирает правки в отслеживаемых файлах,
# а новые файлы оставляет на месте. Одной строкой это звучало бы как ложь
# в одну или в другую сторону.
changed="$(git status --porcelain --untracked-files=no || true)"
if [ -n "$changed" ]; then
    echo "— правки в файлах репозитория (они пропадут безвозвратно):"
    echo "$changed"
fi
untracked="$(git ls-files --others --exclude-standard || true)"
if [ -n "$untracked" ]; then
    echo "— посторонние файлы (останутся на месте, их никто не трогает):"
    echo "$untracked" | head -10
fi

before_reqs="$(git hash-object daemon/requirements.txt daemon/requirements-dev.txt 2>/dev/null || true)"
git reset --hard -q "origin/$BRANCH"
echo "— стало: $(git log --oneline -1)"
after_reqs="$(git hash-object daemon/requirements.txt daemon/requirements-dev.txt 2>/dev/null || true)"

# Раньше этого шага не было, и служба поднималась на старых зависимостях:
# код новый, библиотека старая, а в ухо приходит невнятная ошибка.
if [ "$before_reqs" != "$after_reqs" ] && [ -x "$ROOT/.venv/bin/pip" ]; then
    echo "— зависимости изменились, доставляю"
    "$ROOT/.venv/bin/pip" install --quiet -r "$ROOT/daemon/requirements-dev.txt" \
        || die "не встали зависимости: $ROOT/.venv/bin/pip install -r $ROOT/daemon/requirements-dev.txt"
fi

# Быстрая проверка: спека и код должны хотя бы сходиться между собой.
# Полные тесты здесь не гоняем — это минуты, а человек ждёт.
if [ -x "$ROOT/.venv/bin/python" ]; then
    "$ROOT/.venv/bin/python" "$ROOT/scripts/validate_spec.py" \
        || die "спека и код разошлись — служба не перезапущена, старая версия работает.
Откат: git -C $ROOT reset --hard $STAMP"
fi

# Новые настройки, которых у старой установки быть не могло. Дописываем
# только недостающее: то, что человек поправил руками, остаётся как есть.
add_env() {
    grep -q "^$1=" "$ENV_FILE" 2>/dev/null && return 0
    printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"
    echo "— новая настройка: $1=$2"
}
if [ -f "$ENV_FILE" ]; then
    PUBLIC="${PUBLIC_DIR:-$ROOT/public}"
    mkdir -p "$PUBLIC"
    PORT_NOW="$(sed -n 's/^PORT=//p' "$ENV_FILE" | tail -1)"
    # Домен знает Caddy — если он стоит, адрес публикации https и без порта.
    DOMAIN_NOW="$(sed -n 's/^\([A-Za-z0-9.-]*\) {$/\1/p' /etc/caddy/Caddyfile 2>/dev/null | head -1)"
    if [ -n "$DOMAIN_NOW" ]; then
        BASE="https://$DOMAIN_NOW"
    else
        BASE="http://$(hostname -I 2>/dev/null | awk '{print $1}'):${PORT_NOW:-8787}"
    fi
    add_env PUBLIC_DIR "$PUBLIC"
    add_env PUBLIC_URL "$BASE/p"
    add_env MCP_CONFIG "$ROOT/mcp.json"
fi

# Инструменты, которыми Claude Code делает то, чего не умеет сам. Ставим
# только недостающее и только на apt-системах; не встало — не беда, приписка
# к промпту говорит сессии правду о том, что на машине есть.
if [ "${TOOLBOX:-1}" = "1" ] && command -v apt-get >/dev/null; then
    missing=""
    command -v ffmpeg  >/dev/null || missing="$missing ffmpeg"
    command -v magick  >/dev/null || command -v convert >/dev/null || missing="$missing imagemagick"
    command -v rg      >/dev/null || missing="$missing ripgrep"
    command -v jq      >/dev/null || missing="$missing jq"
    command -v pandoc  >/dev/null || missing="$missing pandoc"
    command -v chromium >/dev/null || command -v chromium-browser >/dev/null \
        || missing="$missing chromium chromium-browser"
    if [ -n "$missing" ]; then
        echo "— доставляю инструменты:$missing (не обязательно, можно TOOLBOX=0)"
        DEBIAN_FRONTEND=noninteractive apt-get update -qq || true
        for pkg in $missing; do
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$pkg" 2>/dev/null || true
        done
    fi
fi

command -v systemctl >/dev/null 2>&1 \
    || die "нет systemd — перезапусти демон тем способом, которым запускал"
systemctl restart voice-shell \
    || die "не смог перезапустить службу.
Если её нет — install-server.sh. Если нет прав — запусти через sudo."
sleep 2
systemctl is-active voice-shell >/dev/null 2>&1 \
    && echo "— служба: перезапущена" \
    || { echo "::служба не поднялась::"; journalctl -u voice-shell -n 20 --no-pager; exit 1; }

PORT_VALUE="$(sed -n 's/^PORT=//p' "$ENV_FILE" 2>/dev/null | tail -1)"
TOKEN_VALUE="$(sed -n 's/^VOICE_TOKEN=//p' "$ENV_FILE" 2>/dev/null | tail -1)"
echo "— что думает демон:"
curl -fsS --max-time 5 "http://127.0.0.1:${PORT_VALUE:-8787}/healthz?token=${TOKEN_VALUE}" \
    || echo "  не ответил — запусти $ROOT/scripts/doctor.sh"

echo
echo "— прежнее состояние помечено как $STAMP"
echo "  откат целиком: git -C $ROOT reset --hard $STAMP && systemctl restart voice-shell"
