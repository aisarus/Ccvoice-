#!/usr/bin/env bash
# Свой ключ подписи APK — и две строки, которые надо вставить в секреты GitHub.
#
#   bash scripts/release-key.sh
#   bash scripts/release-key.sh --out ~/keys/voice-shell-release.keystore
#
# Зачем. В репозитории лежит android/app/voice-shell.keystore, а пароль к нему —
# открытым текстом в android/app/build.gradle.kts. Пока репозиторий был
# приватным, это была просто удобная мелочь: CI подписывал каждую сборку одним
# ключом, и обновление вставало поверх предыдущего. В публичном репозитории тем
# же ключом APK подпишет кто угодно, и Android поставит чужую сборку
# обновлением поверх настоящего приложения — подпись совпала, значит «свой».
#
# Этот скрипт заводит ключ, который знаешь только ты. Файл ключа остаётся на
# твоей машине и в репозиторий не попадает: .gitignore его не пустит.
#
# ВАЖНО: ключ невосстановим. Потеряешь файл или пароль — обновления поверх уже
# установленного приложения больше не встанут никогда, только переустановка с
# потерей данных. Положи копию туда же, где лежат остальные твои пароли.
set -euo pipefail

ALIAS="voiceshell"           # так же называется ключ в build.gradle.kts
VALIDITY_DAYS=10000          # ~27 лет: короче срока жизни приложения не бывает
OUT="${VOICE_KEYSTORE_OUT:-$HOME/.voice-shell/release.keystore}"

while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT="${2:?--out без пути}"; shift 2 ;;
        -h|--help) sed -n '2,19p' "$0" | cut -c3-; exit 0 ;;
        *) echo "не знаю такого ключа: $1" >&2; exit 2 ;;
    esac
done

die() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

command -v keytool >/dev/null 2>&1 \
    || die "нет keytool — поставь JDK 17 (apt install openjdk-17-jdk-headless)"
command -v base64 >/dev/null 2>&1 || die "нет base64"

# Перезапись существующего ключа необратима, поэтому её здесь просто нет.
[ -e "$OUT" ] && die "файл уже есть: $OUT
Похоже, это твой рабочий ключ. Перезаписать его — значит навсегда потерять
возможность обновлять уже установленное приложение. Если ключ правда не нужен,
убери файл руками и запусти снова."

say "Пароль"
cat <<'HOWTO'
Придумай пароль и сохрани его там же, где хранишь остальные. Он понадобится
второй раз — в секретах GitHub. Восстановить его нельзя.
HOWTO

PASSWORD="${VOICE_KEYSTORE_PASSWORD:-}"
if [ -z "$PASSWORD" ]; then
    read -r -s -p "Пароль (минимум 6 знаков): " PASSWORD < /dev/tty || true
    echo
    read -r -s -p "Ещё раз: " AGAIN < /dev/tty || true
    echo
    [ "$PASSWORD" = "${AGAIN:-}" ] || die "пароли разные"
fi
[ "${#PASSWORD}" -ge 6 ] || die "keytool короче шести знаков не примет"

mkdir -p "$(dirname "$OUT")"
# Ключ не должен быть читаем никому, кроме владельца, — с самого создания.
umask 077

say "Ключ"
# -dname без вопросов: на проверку подписи в Android эти поля не влияют, а
# диалог из шести строк — верный способ бросить настройку на середине.
keytool -genkeypair -noprompt \
    -keystore "$OUT" \
    -storetype PKCS12 \
    -storepass "$PASSWORD" \
    -keypass "$PASSWORD" \
    -alias "$ALIAS" \
    -keyalg RSA -keysize 4096 \
    -validity "$VALIDITY_DAYS" \
    -dname "CN=Voice Shell, OU=Release, O=Voice Shell, C=--" \
    >/dev/null

chmod 600 "$OUT"

FINGERPRINT="$(keytool -list -v -keystore "$OUT" -storepass "$PASSWORD" \
    | sed -n 's/.*SHA256: *//p' | head -1)"

say "Готово: $OUT"
echo "Отпечаток ключа (SHA-256):"
echo "  $FINGERPRINT"
echo
echo "Тем же отпечатком потом проверяется любой APK:"
echo "  apksigner verify --print-certs app-debug.apk"

say "Что вставить в секреты GitHub"
cat <<HOWTO
Settings → Secrets and variables → Actions → New repository secret.
Два секрета, имена — ровно такие:

  ANDROID_KEYSTORE_BASE64     значение — одна длинная строка, она ниже
  ANDROID_KEYSTORE_PASSWORD   значение — пароль, который ты только что придумал

Получить эту строку заново можно так:

  base64 -w0 "$OUT"

У macOS в base64 нет -w0, там так:

  base64 "$OUT" | tr -d '\\n'
HOWTO

say "Печатаю прямо сейчас — выделяй от первого знака до последнего"
echo
if base64 -w0 "$OUT" 2>/dev/null; then
    echo
else
    base64 "$OUT" | tr -d '\n'
    echo
fi

cat <<'AFTER'

Когда оба секрета вставлены — запусти сборку: Actions → android → Run workflow.
В шаге «Проверить, каким ключом подписан APK» должно быть написано, что подпись
твоя, и напечатан тот же отпечаток, что выше. Если там сказано, что ключ из
секрета не применился, — значит в android/app/build.gradle.kts ещё не сделана
правка, о которой этот шаг пишет прямым текстом.

Пока секретов нет, CI подписывает сборку открытым отладочным ключом из
репозитория. Это работает, но такую сборку нельзя выдавать за релиз.

Один раз после смены ключа приложение придётся снести и поставить заново:
Android не ставит поверх APK, подписанный другим ключом. Дальше обновления
снова встают поверх — но уже только твои.
AFTER
