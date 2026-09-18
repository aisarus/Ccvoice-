#!/usr/bin/env bash
# Установка voice-claude-daemon на компьютер. macOS и Linux.
# Машиночитаемая версия этой инструкции: docs/desktop-handoff.json
set -euo pipefail

# Скрипт сбрасывает репозиторий, в котором лежит сам. bash читает файл по
# мере выполнения, по смещению в байтах, и `git reset --hard` меняет этот
# файл под ним: дальше выполнение уходит в середину чужой строки или молча
# упирается в конец. Ноль на выходе, половина работы сделана. Уходим в копию
# до первой правки — её переписать некому.
#
# Проверка на `.sh` не лишняя: при запуске через `curl | bash` в $0 лежит
# «bash», копировать надо не его, да и переписывать под таким запуском нечего.
VOICE_SHELL_SELF="${VOICE_SHELL_SELF:-$0}"
if [ -z "${VOICE_SHELL_SELF_COPY:-}" ]; then
    case "$0" in
        *.sh)
            SELF_COPY="$(mktemp "${TMPDIR:-/tmp}/voice-shell-run.XXXXXX")"
            cat "$0" > "$SELF_COPY"
            export VOICE_SHELL_SELF VOICE_SHELL_SELF_COPY="$SELF_COPY"
            exec bash "$SELF_COPY" "$@"
            ;;
    esac
else
    trap 'rm -f "$VOICE_SHELL_SELF_COPY"' EXIT
fi
ROOT="${VOICE_SHELL_DIR:-$HOME/voice-shell}"
BRANCH="claude/voice-shell-claude-code-77wwh2"
WORKSPACE="${1:-$ROOT}"
PORT="${PORT:-8787}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1. Репозиторий"
if [ -d "$ROOT/.git" ]; then
  # Не pull: на разошедшихся ветках он останавливается на «divergent branches»
  # и требует выбрать способ слияния. Нужна ровно та версия, что в ветке,
  # поэтому fetch + reset — но сначала метка, по которой прежнее вернётся.
  git -C "$ROOT" fetch origin "$BRANCH" || {
    echo "не смог забрать ветку $BRANCH — проверь сеть"; exit 1; }
  STAMP="before-install-$(date +%Y%m%d-%H%M%S)"
  git -C "$ROOT" tag -f "$STAMP" HEAD >/dev/null 2>&1 || true
  changed="$(git -C "$ROOT" status --porcelain --untracked-files=no)"
  [ -n "$changed" ] && { echo "правки в $ROOT пропадут:"; echo "$changed"; }
  git -C "$ROOT" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
  git -C "$ROOT" reset --hard --quiet "origin/$BRANCH"
  echo "прежнее состояние: git -C $ROOT reset --hard $STAMP"
else
  git clone https://github.com/aisarus/Ccvoice-.git "$ROOT"
  git -C "$ROOT" checkout "$BRANCH"
fi

say "2. Окружение"
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --quiet --upgrade pip
"$ROOT/.venv/bin/pip" install --quiet -r "$ROOT/daemon/requirements-dev.txt"

say "3. Claude Code CLI"
if ! command -v claude >/dev/null 2>&1; then
  npm install -g @anthropic-ai/claude-code
fi
claude --version || { echo "CLI не установился — поставь Node 18+ и повтори"; exit 1; }

say "4. Самопроверка"
(cd "$ROOT" && .venv/bin/python -m pytest tests -q && .venv/bin/python scripts/validate_spec.py) \
  || { echo "тесты не прошли — дальше идти нельзя, демон не запускаю."
       echo "Повторить руками: cd $ROOT && .venv/bin/python -m pytest tests"; exit 1; }

say "5. Токен доступа"
TOKEN="${VOICE_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')}"

say "Готово. Запускаю демон."
echo "  рабочий каталог : $WORKSPACE"
echo "  порт            : $PORT"
echo "  токен           : $TOKEN"
echo
echo "В приложении на телефоне укажи адрес http://<имя-машины-в-tailnet>:$PORT и этот токен."
echo "Для браузерного клиента вне дома: tailscale serve --bg $PORT, заходить по https."
echo

cd "$ROOT/daemon"
VOICE_TOKEN="$TOKEN" exec "$ROOT/.venv/bin/python" -m voice_claude --workspace "$WORKSPACE" --port "$PORT"
