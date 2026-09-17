#!/usr/bin/env bash
# Дымовая проверка на эмуляторе: приложение должно установиться, открыться,
# пережить выдачу разрешений и не соврать про то, чем оно слушает.
set -euo pipefail

APK="android/app/build/outputs/apk/debug/app-debug.apk"
PKG="com.voiceshell"

fail() { echo "::error::$1"; shift; "$@" || true; exit 1; }

echo "== установка =="
adb install -r "$APK"

echo "== запуск экрана =="
adb logcat -c
adb shell am start -n "$PKG/.MainActivity"
sleep 8

crash=$(adb logcat -d -b crash || true)
[ -z "$crash" ] || fail "приложение упало при старте" echo "$crash"
[ -n "$(adb shell pidof $PKG | tr -d '\r')" ] || fail "процесс не живёт" adb logcat -d -t 120

echo "== разрешения =="
adb shell pm grant "$PKG" android.permission.RECORD_AUDIO
adb shell pm grant "$PKG" android.permission.POST_NOTIFICATIONS

# Службу из оболочки запустить нельзя: она не exported, и `am
# start-foreground-service` отвечает «Requires permission not exported from
# uid ...». Раньше эта ошибка гасилась через `|| true`, и всё, что шло дальше,
# проверяло живой экран, делая вид, что проверяет службу. Поэтому код, который
# трогает системный звук, проверяется инструментальными тестами — они идут
# внутри самого приложения, с его правами.
echo "== инструментальные тесты =="
adb logcat -c
( cd android && gradle --no-daemon connectedDebugAndroidTest )

crash=$(adb logcat -d -b crash || true)
[ -z "$crash" ] || fail "что-то упало во время тестов" echo "$crash"

echo "== что сказало приложение =="
adb logcat -d -s VoiceShell:* | tail -20 || true

echo "OK: приложение живо, маршрут микрофона проверен на устройстве"
