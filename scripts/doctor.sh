#!/usr/bin/env bash
# Один отчёт обо всём состоянии: что за код, жива ли служба, что с токенами,
# отвечает ли демон. Запускать на сервере, вывод показывать целиком.
#
#   sudo bash /opt/voice-shell/scripts/doctor.sh          полный отчёт
#   sudo bash /opt/voice-shell/scripts/doctor.sh --fast   без двух долгих проверок
#
# Долгие — прямой вопрос к CLI и сквозная реплика через демон: вместе до трёх
# минут. Когда нужен быстрый ответ «жив или нет», хватает --fast.
#
# Код возврата: 0 — проблем не нашёл, 1 — нашёл (список в конце).
#
# Переменные:
#   VOICE_SHELL_DIR  рабочая копия (по умолчанию /opt/voice-shell)
#   VOICE_ENV_FILE   файл окружения службы (по умолчанию /etc/voice-shell.env)

ROOT="${VOICE_SHELL_DIR:-/opt/voice-shell}"
ENV_FILE="${VOICE_ENV_FILE:-/etc/voice-shell.env}"
FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

PROBLEMS=()
note() { PROBLEMS+=("$*"); }
line() { printf '\n— %s\n' "$*"; }

# Значение переменной из файла окружения. sed, а не grep -oP: PCRE есть не
# в каждом grep, а молчащий grep выглядит как «переменная не задана».
value_of() {
    [ -r "$ENV_FILE" ] || return 0
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -1
}

PY=""
for candidate in "$ROOT/.venv/bin/python" python3; do
    command -v "$candidate" >/dev/null 2>&1 && { PY="$candidate"; break; }
    [ -x "$candidate" ] && { PY="$candidate"; break; }
done

line "код"
if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "$ROOT" log --oneline -1
    branch="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)"
    echo "ветка: $branch"
    if git -C "$ROOT" rev-parse --verify --quiet "origin/$branch" >/dev/null 2>&1; then
        behind="$(git -C "$ROOT" rev-list --count "HEAD..origin/$branch" 2>/dev/null || echo 0)"
        if [ "${behind:-0}" -gt 0 ]; then
            echo "отстаёт от origin на $behind коммит(ов)"
            note "код устарел на $behind коммит(ов) — обнови: sudo bash $ROOT/scripts/update-server.sh"
        else
            echo "свежий относительно origin/$branch (на момент последнего fetch)"
        fi
    fi
    dirty="$(git -C "$ROOT" status --porcelain 2>/dev/null)"
    [ -n "$dirty" ] && echo "правки в рабочей копии: $(printf '%s\n' "$dirty" | wc -l) файл(ов) — update-server.sh их сотрёт"
else
    echo "нет репозитория в $ROOT"
    note "нет рабочей копии в $ROOT — поставь заново install-server.sh"
fi

line "служба"
if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemd нет — служба тут не живёт, демон запускается руками"
elif ! systemctl list-unit-files voice-shell.service >/dev/null 2>&1 \
     || [ "$(systemctl is-enabled voice-shell 2>/dev/null)" = "not-found" ]; then
    echo "служба voice-shell не установлена"
    note "служба не установлена — install-server.sh"
else
    active="$(systemctl is-active voice-shell 2>/dev/null)"
    enabled="$(systemctl is-enabled voice-shell 2>/dev/null)"
    echo "сейчас: $active · автозапуск: $enabled"
    [ "$active" = "active" ] || note "служба не работает ($active) — journalctl -u voice-shell -n 50"
    [ "$enabled" = "enabled" ] || note "служба не поднимется после перезагрузки — systemctl enable voice-shell"
fi

line "настройки"
if [ -r "$ENV_FILE" ]; then
    PORT_VALUE="$(value_of PORT)"
    PORT_VALUE="${PORT_VALUE:-8787}"
    echo "файл: $ENV_FILE"
    echo "порт: $PORT_VALUE"
    echo "рабочий каталог: $(value_of WORKSPACE_DIR)"
    for name in VOICE_TOKEN CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY; do
        v="$(value_of "$name")"
        if [ -z "$v" ]; then
            echo "$name: не задан"
        elif printf '%s' "$v" | LC_ALL=C grep -q '[^ -~]'; then
            echo "$name: ПОРТИТСЯ — есть не-ASCII символы (${#v} симв.)"
            note "$name испорчен: в нём не-ASCII символы. Вставь токен заново, без переносов и кавычек"
        elif [ "$name" = "VOICE_TOKEN" ]; then
            # У ключей Claude первые девять символов — фиксированный «sk-ant-oa».
            # VOICE_TOKEN случайный целиком, и этот вывод люди вставляют в
            # публичный issue: показываем только длину.
            echo "$name: ok, ${#v} симв. (значение не печатаем — это пароль к демону)"
        else
            echo "$name: ok, ${#v} симв., начинается с ${v:0:9}…"
        fi
    done
    [ -z "$(value_of VOICE_TOKEN)" ] && note "VOICE_TOKEN не задан — телефон не сможет подключиться"
else
    echo "нет файла $ENV_FILE"
    note "нет файла окружения $ENV_FILE — служба не знает ни токена, ни порта"
    PORT_VALUE=8787
fi

line "библиотека Claude"
# Отдельная проверка, потому что /healthz этого не различает: он покажет
# «credential: cli» и при этом «code: false», если пакета просто нет.
if [ -n "$PY" ] && "$PY" -c "import claude_agent_sdk" >/dev/null 2>&1; then
    echo "claude-agent-sdk установлен"
else
    echo "claude-agent-sdk НЕ установлен — цели «код» и «чат» будут отвечать заглушкой"
    note "нет пакета claude-agent-sdk: $ROOT/.venv/bin/pip install -r $ROOT/daemon/requirements.txt"
fi

line "вход CLI"
CRED="${CLAUDE_CONFIG_DIR:-/root/.claude}/.credentials.json"
if [ -s "$CRED" ]; then
    echo "CLI авторизован сам ($CRED) — переменная с токеном не обязательна"
else
    echo "файла входа нет ($CRED) — это ещё не отказ: демон спрашивает сам CLI"
fi
if [ "$FAST" = 1 ]; then
    echo "прямая проверка CLI: пропущена (--fast)"
elif ! command -v claude >/dev/null 2>&1; then
    echo "прямая проверка CLI: claude не установлен"
    note "нет CLI claude — npm install -g @anthropic-ai/claude-code"
else
    echo -n "прямая проверка CLI (до 90 с): "
    # Ввод закрываем явно: по ssh это tty, и CLI может ждать его до таймаута.
    cli_said="$(timeout 90 env -u CLAUDE_CODE_OAUTH_TOKEN claude -p "ответь одним словом: работает" \
                </dev/null 2>&1 | head -3)"
    if [ -n "$cli_said" ]; then
        printf '%s\n' "$cli_said"
    else
        echo "промолчал"
        note "CLI claude не ответил за 90 с — войди заново: claude setup-token"
    fi
fi

line "порт"
if command -v ss >/dev/null 2>&1; then
    ss -lntp 2>/dev/null | grep -E ":${PORT_VALUE}\b" || echo "никто не слушает $PORT_VALUE"
elif command -v netstat >/dev/null 2>&1; then
    netstat -lntp 2>/dev/null | grep -E ":${PORT_VALUE}\b" || echo "никто не слушает $PORT_VALUE"
else
    # Раньше здесь печаталось «никто не слушает» просто потому, что нет ss.
    echo "нечем посмотреть (нет ss и netstat) — смотри ответ /healthz ниже"
fi

line "что думает сам демон"
TOKEN_VALUE="$(value_of VOICE_TOKEN)"
HEALTH="$(curl -fsS --max-time 5 "http://127.0.0.1:${PORT_VALUE}/healthz?token=${TOKEN_VALUE}" 2>/dev/null)"
if [ -z "$HEALTH" ]; then
    echo "healthz не отвечает на 127.0.0.1:${PORT_VALUE}"
    note "демон не отвечает на /healthz — journalctl -u voice-shell -n 50"
else
    printf '%s\n' "$HEALTH"
    if [ "${HEALTH#\{}" = "$HEALTH" ]; then
        # Пришло «ok» вместо JSON: токен не подошёл.
        echo "(ответил «ok» — значит VOICE_TOKEN не совпал с тем, что в $ENV_FILE)"
        note "VOICE_TOKEN из $ENV_FILE не подходит работающему демону — перезапусти службу"
    elif [ -n "$PY" ]; then
        field() { printf '%s' "$HEALTH" | "$PY" -c \
            "import json,sys;print(json.load(sys.stdin).get('$1',''))" 2>/dev/null; }
        [ "$(field code)" = "True" ] || note "цель «код» в заглушке — см. «библиотека Claude» и «вход CLI» выше"
        [ "$(field chat)" = "True" ] || note "цель «чат» в заглушке — та же причина, что и у «кода»"
        problem="$(field credential_problem)"
        [ -n "$problem" ] && [ "$problem" != "None" ] && note "доступ к Claude: $problem"
    fi
fi

line "последние логи"
if command -v journalctl >/dev/null 2>&1; then
    logs="$(journalctl -u voice-shell -n 12 --no-pager 2>/dev/null)"
    [ -n "$logs" ] && printf '%s\n' "$logs" || echo "логов нет"
else
    echo "journalctl нет"
fi

line "сквозная проверка"
if [ "$FAST" = 1 ]; then
    echo "пропущена (--fast)"
elif [ ! -f "$ROOT/scripts/say.py" ]; then
    echo "нет $ROOT/scripts/say.py — обнови код: sudo bash $ROOT/scripts/update-server.sh"
    note "в рабочей копии нет scripts/say.py — код устарел"
elif [ ! -x "$ROOT/.venv/bin/python" ]; then
    echo "нет $ROOT/.venv/bin/python — окружение не собрано"
    note "нет venv в $ROOT/.venv — переустанови: install-server.sh"
else
    VOICE_ENV_FILE="$ENV_FILE" "$ROOT/.venv/bin/python" "$ROOT/scripts/say.py" "скажи привет" \
        || note "сквозная реплика не прошла — смотри её вывод выше"
fi

line "итог"
if [ ${#PROBLEMS[@]} -eq 0 ]; then
    echo "проблем не нашёл"
    exit 0
fi
printf 'нашёл %d:\n' "${#PROBLEMS[@]}"
for p in "${PROBLEMS[@]}"; do printf '  · %s\n' "$p"; done
exit 1
