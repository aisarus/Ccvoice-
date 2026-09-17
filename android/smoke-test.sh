#!/usr/bin/env bash
# Дымовая проверка на эмуляторе: приложение должно установиться, открыться,
# пережить выдачу разрешений и запуск фоновой службы.
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

echo "== разрешения и служба =="
adb shell pm grant "$PKG" android.permission.RECORD_AUDIO
adb shell pm grant "$PKG" android.permission.POST_NOTIFICATIONS
adb logcat -c
adb shell am start-foreground-service -n "$PKG/.VoiceService" || true
sleep 10

crash=$(adb logcat -d -b crash || true)
[ -z "$crash" ] || fail "служба упала" echo "$crash"
[ -n "$(adb shell pidof $PKG | tr -d '\r')" ] || fail "процесс умер после запуска службы" adb logcat -d -t 200

echo "== что сказала служба =="
adb logcat -d -s VoiceShell:* | tail -20 || true

echo "OK: приложение и служба живы"
