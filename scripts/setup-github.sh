#!/usr/bin/env bash
# Доступ к GitHub для Claude: ставит gh, кладёт токен в окружение службы,
# настраивает git и проверяет, что всё работает.
#
#   sudo bash scripts/setup-github.sh ghp_ТВОЙ_ТОКЕН "Имя Фамилия" почта@example.com
#
# Токен: github.com/settings/tokens -> Generate new token (classic).
# Права: repo (всё), workflow. delete_repo брать не советую — удаление
# репозитория необратимо, а голосом это слишком легко сказать.
set -euo pipefail

TOKEN="${1:-${GH_TOKEN:-}}"
GIT_NAME="${2:-Claude}"
GIT_EMAIL="${3:-claude@voice-shell.local}"
ENV_FILE="${VOICE_ENV_FILE:-/etc/voice-shell.env}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

[ -n "$TOKEN" ] || die "нужен токен: sudo bash scripts/setup-github.sh ghp_..."
case "$TOKEN" in
    ghp_*|github_pat_*) : ;;
    *) die "не похоже на токен GitHub — он начинается с ghp_ или github_pat_" ;;
esac
[ "$(id -u)" -eq 0 ] || die "нужен root: скрипт пишет в $ENV_FILE и в общий git-конфиг.
Запусти через sudo bash scripts/setup-github.sh ..."
[ -f "$ENV_FILE" ] || die "нет файла окружения $ENV_FILE — сначала install-server.sh.
Если он в другом месте: VOICE_ENV_FILE=/путь sudo -E bash scripts/setup-github.sh ..."

say "gh"
if ! command -v gh >/dev/null; then
    if command -v apt-get >/dev/null; then
        curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
            | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg
        chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
            > /etc/apt/sources.list.d/github-cli.list
        apt-get update -qq && apt-get install -y -qq gh
    elif command -v dnf >/dev/null; then
        dnf install -y -q gh
    else
        die "поставь gh вручную: https://cli.github.com"
    fi
fi
gh --version | head -1

say "вход"
export GH_TOKEN="$TOKEN"
# Проверяем кодом возврата самого gh, а не хвостом конвейера: `gh ... | head`
# всегда возвращает успех head'а, и негодный токен проходил насквозь.
if status_text="$(gh auth status 2>&1)"; then
    printf '%s\n' "$status_text" | head -3
else
    printf '%s\n' "$status_text" | head -5
    die "токен не принят GitHub — проверь, что он не истёк и у него есть права repo и workflow"
fi

say "git"
git config --system user.name "$GIT_NAME"
git config --system user.email "$GIT_EMAIL"
gh auth setup-git

say "окружение службы"
umask 077
sed -i '/^GH_TOKEN=/d;/^GITHUB_TOKEN=/d' "$ENV_FILE"
{
    echo "GH_TOKEN=$TOKEN"
    echo "GITHUB_TOKEN=$TOKEN"
} >> "$ENV_FILE"
chmod 600 "$ENV_FILE"
systemctl restart voice-shell 2>/dev/null || true

say "проверка"
echo -n "аккаунт: "; gh api user --jq .login
echo "доступные репозитории (первые пять):"
gh repo list --limit 5 --json nameWithOwner --jq '.[].nameWithOwner' | sed 's/^/  /'

cat <<'REPORT'

Готово. Теперь голосом работают, например:

  «Клод, покажи мои репозитории»
  «Клод, создай репозиторий voice-notes и положи туда README»
  «Клод, посмотри последние пул-реквесты в Ccvoice»
  «Клод, сделай коммит и запушь»

REPORT
