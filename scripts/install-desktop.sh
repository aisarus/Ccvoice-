#!/usr/bin/env bash
# Установка voice-claude-daemon на компьютер. macOS и Linux.
# Машиночитаемая версия этой инструкции: docs/desktop-handoff.json
set -euo pipefail

ROOT="${VOICE_SHELL_DIR:-$HOME/voice-shell}"
BRANCH="claude/voice-shell-claude-code-77wwh2"
WORKSPACE="${1:-$ROOT}"
PORT="${PORT:-8787}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1. Репозиторий"
if [ -d "$ROOT/.git" ]; then
  git -C "$ROOT" fetch origin "$BRANCH" && git -C "$ROOT" checkout "$BRANCH" && git -C "$ROOT" pull origin "$BRANCH"
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
(cd "$ROOT" && .venv/bin/python -m pytest tests -q && .venv/bin/python scripts/validate_spec.py)

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
