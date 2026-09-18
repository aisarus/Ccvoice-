#!/usr/bin/env bash
# Одна команда от «сервер стоит» до «можно работать голосом».
#
#   curl -fsSL https://raw.githubusercontent.com/aisarus/Ccvoice-/claude/voice-shell-claude-code-77wwh2/scripts/setup-work.sh | sudo bash
#
# Спрашивает ровно одно — токен GitHub, скрытым вводом, и только если его
# ещё нет в окружении службы. Остальное берёт откуда может: имя и почту —
# у самого GitHub по этому токену, список репозиториев — у gh, адрес — у
# сети. Ничего вписывать руками не надо.
#
# Репозитории можно назвать аргументами, и тогда вопросов не будет вовсе:
#
#   ... | sudo bash -s -- aisarus/Ccvoice- кто-то/другой-проект
#
# Токен аргументом не принимается нарочно: в argv он виден всей машине
# через ps и остаётся в истории оболочки. Скрытый ввод или переменная
# GH_TOKEN — оба пути не оставляют следа.
#
# Переменные:
#   VOICE_SHELL_DIR  рабочая копия (по умолчанию /opt/voice-shell)
#   VOICE_ENV_FILE   файл окружения службы (по умолчанию /etc/voice-shell.env)
set -euo pipefail

ROOT="${VOICE_SHELL_DIR:-/opt/voice-shell}"
ENV_FILE="${VOICE_ENV_FILE:-/etc/voice-shell.env}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# Ставят через curl | sudo bash, и тогда stdin — это сам скрипт, спросить
# через него нельзя. Живой терминал доступен как /dev/tty, оттуда и спрашиваем.
have_human() { [ -t 0 ] || [ -r /dev/tty ]; }
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
ask_secret() {
    local prompt="$1" answer=""
    if [ -t 0 ]; then
        read -r -s -p "$prompt" answer || true
        echo
    elif [ -r /dev/tty ]; then
        printf '%s' "$prompt" > /dev/tty
        read -r -s answer < /dev/tty || true
        echo > /dev/tty
    fi
    printf '%s' "$answer"
}

[ "$(id -u)" -eq 0 ] || die "нужен root: запусти через sudo"
[ -d "$ROOT/.git" ] || die "нет рабочей копии в $ROOT — сначала установка:
  curl -fsSL https://raw.githubusercontent.com/aisarus/Ccvoice-/claude/voice-shell-claude-code-77wwh2/scripts/install-server.sh | sudo bash"

# ---------------------------------------------------------------- обновление
say "Обновление сервера"
bash "$ROOT/scripts/update-server.sh"

# -------------------------------------------------------------------- токен
say "Доступ к GitHub"
TOKEN="${GH_TOKEN:-}"
[ -n "$TOKEN" ] || TOKEN="$(sed -n 's/^GH_TOKEN=//p' "$ENV_FILE" 2>/dev/null | tail -1)"
if [ -n "$TOKEN" ]; then
    echo "токен уже прописан в $ENV_FILE — беру его"
else
    have_human || die "токена нет и спросить некого.
Передай его переменной: GH_TOKEN=ghp_... sudo -E bash $0
Взять: github.com/settings/tokens -> classic, права repo и workflow."
    echo "Токен: github.com/settings/tokens -> Generate new token (classic)."
    echo "Права: repo (всё) и workflow. delete_repo брать не советую —"
    echo "удаление необратимо, а голосом это слишком легко сказать."
    TOKEN="$(ask_secret 'Вставь токен (ввод не отображается): ')"
fi
TOKEN="$(printf '%s' "$TOKEN" | tr -d '[:space:]')"
[ -n "$TOKEN" ] || die "пустой токен"

# Имя и почту спрашивать незачем: GitHub знает их по этому же токену.
# Почта у многих скрыта — тогда берём ту, что GitHub для этого и заводит.
IDENTITY="$(curl -fsSL --max-time 20 \
    -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/vnd.github+json" \
    https://api.github.com/user)" \
    || die "GitHub не принял токен — проверь, что он не истёк и у него есть права repo и workflow"
read -r LOGIN NAME EMAIL <<<"$(printf '%s' "$IDENTITY" | python3 -c '
import json, sys
u = json.load(sys.stdin)
login = u["login"]
name = (u.get("name") or login).replace(" ", " ")
email = u.get("email") or f'"'"'{u["id"]}+{login}@users.noreply.github.com'"'"'
print(login, name, email)
')"
NAME="${NAME//$' '/ }"
echo "аккаунт: $LOGIN — $NAME <$EMAIL>"

bash "$ROOT/scripts/setup-github.sh" "$TOKEN" "$NAME" "$EMAIL"

# --------------------------------------------------------------- проекты
WS="$(sed -n 's/^WORKSPACE_DIR=//p' "$ENV_FILE" 2>/dev/null | tail -1)"
WS="${WS:-$ROOT/workspace}"

say "Проекты"
echo "Каждый кладётся отдельной папкой в $WS."
echo "Дальше голосом: «Клод, в папке <имя> посмотри, почему…»"

WANTED=("$@")
if [ ${#WANTED[@]} -eq 0 ] && have_human; then
    mapfile -t AVAILABLE < <(GH_TOKEN="$TOKEN" gh repo list --limit 30 \
        --json nameWithOwner,isPrivate,updatedAt \
        --jq 'sort_by(.updatedAt) | reverse | .[] | .nameWithOwner' 2>/dev/null || true)
    if [ ${#AVAILABLE[@]} -gt 0 ]; then
        echo
        for i in "${!AVAILABLE[@]}"; do
            printf '  %2d) %s\n' "$((i + 1))" "${AVAILABLE[$i]}"
        done
        echo
        CHOICE="$(ask 'Номера через пробел (Enter — ни одного): ')"
        for n in $CHOICE; do
            case "$n" in
                ''|*[!0-9]*) continue ;;
            esac
            [ "$n" -ge 1 ] && [ "$n" -le ${#AVAILABLE[@]} ] && WANTED+=("${AVAILABLE[$((n - 1))]}")
        done
    fi
fi

CLONED=()
SEEN=" "
for repo in ${WANTED[@]+"${WANTED[@]}"}; do
    case "$SEEN" in *" $repo "*) continue ;; esac
    SEEN="$SEEN$repo "
    name="$(basename "$repo" .git)"
    target="$WS/$name"
    if [ -e "$target" ]; then
        echo "— $name уже лежит в рабочей папке, не трогаю"
        CLONED+=("$name")
        continue
    fi
    # Ошибку клонирования не прячем: «не вышло» без причины — это вопрос
    # ко мне вместо ответа, а причина обычно прямо в тексте git.
    if GH_TOKEN="$TOKEN" gh repo clone "$repo" "$target" -- --quiet; then
        echo "— $name склонирован"
        CLONED+=("$name")
    else
        echo "— $repo склонировать не вышло, причина выше"
    fi
done

# ----------------------------------------------------------------- проверка
say "Проверка"
bash "$ROOT/scripts/doctor.sh" --fast || true

PUBLIC_URL_VALUE="$(sed -n 's/^PUBLIC_URL=//p' "$ENV_FILE" 2>/dev/null | tail -1)"
if [ ${#CLONED[@]} -gt 0 ]; then
    PROJECTS="${CLONED[*]}"
else
    PROJECTS="ни одного. Положить: sudo gh repo clone <владелец/имя> $WS/<имя>"
fi
cat <<REPORT

================================================================
  Можно работать голосом.

  проекты   : $PROJECTS
  публикация: ${PUBLIC_URL_VALUE:-выключена}
  аккаунт   : $LOGIN

  Скажи в наушник, например:

    «Клод, что ты умеешь»
    «Клод, в папке <имя> разберись, почему падает тест, и почини»
    «Клод, исследуй <вопрос> и сделай отчёт»
    «Клод, сделай сайт про <что-нибудь>»
    «Клод, сделай коммит и запушь»

  Не работает — sudo bash $ROOT/scripts/doctor.sh и покажи вывод целиком.
================================================================

REPORT
