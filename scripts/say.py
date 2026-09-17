#!/usr/bin/env python3
"""Сказать демону реплику текстом — без микрофона и без звука.

Проверяет всё, кроме самого распознавания: связь, токен, роутинг, Claude,
подписку и формат ответа. Удобно ночью, в тишине и при отладке.

    python scripts/say.py "скажи привет"
    python scripts/say.py --target code "какие файлы в проекте"
    VOICE_URL=ws://127.0.0.1:8790 VOICE_TOKEN=... python scripts/say.py "привет"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def token_from_env_file(path: str | None = None) -> tuple[str, str]:
    """Достаёт токен и порт из файла службы, чтобы не вводить их руками."""
    token, port = "", "8787"
    file = Path(path or os.environ.get("VOICE_ENV_FILE") or "/etc/voice-shell.env")
    if file.exists():
        for line in file.read_text(encoding="utf-8").splitlines():
            if line.startswith("VOICE_TOKEN="):
                token = line.split("=", 1)[1].strip()
            elif line.startswith("PORT="):
                port = line.split("=", 1)[1].strip()
    return token, port


async def main() -> int:
    parser = argparse.ArgumentParser(description="отправить реплику демону текстом")
    parser.add_argument("text", nargs="+", help="что сказать")
    parser.add_argument("--target", choices=["auto", "code", "chat", "note"], default="auto")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    file_token, file_port = token_from_env_file()
    token = os.environ.get("VOICE_TOKEN", file_token)
    url = os.environ.get("VOICE_URL", f"ws://127.0.0.1:{file_port}")
    if not token:
        print("нет токена: задай VOICE_TOKEN или запусти на сервере, где лежит /etc/voice-shell.env")
        return 2

    from websockets.asyncio.client import connect

    text = " ".join(args.text)
    async with connect(url) as ws:
        await ws.send(json.dumps({"id": "hello", "v": 1, "token": token,
                                  "device_id": "cli", "app_version": "0.3.0"}))
        if args.target != "auto":
            await ws.send(json.dumps({"id": "target_switch", "target": args.target}))
        await ws.send(json.dumps({
            "id": "speech_segment", "segment_id": "cli", "transcript": text,
            "device": "phone_mic", "duration_ms": 1200, "voiced_frames": 40, "role": "master",
        }))

        deadline = asyncio.get_running_loop().time() + args.timeout
        while asyncio.get_running_loop().time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=args.timeout)
            message = json.loads(raw)
            kind = message.get("id")
            if kind == "welcome":
                problem = message.get("credential_problem")
                print(f"доступ: {message.get('credential')}"
                      f"{' — ' + problem if problem else ''}"
                      f" · code: {'ready' if message.get('code_available') else 'stub'}")
            elif kind == "route":
                print(f"цель: {message.get('target')} ({message.get('reason')})")
            elif kind == "permission_request":
                print(f"спрашивает разрешение: {message.get('spoken')}")
            elif kind == "voice_summary":
                if message.get("progress"):
                    # «Работаю» — это не ответ, а признак того, что работа
                    # затянулась. Проверка, которая заканчивается на нём,
                    # показывает успех там, где ответа ещё не было.
                    print(f"… {message.get('text')}")
                    continue
                print(f"\nответ: {message.get('text')}")
                if message.get("stubbed"):
                    print("(это заглушка — Claude недоступен)")
                    return 1
                return 0
            elif kind == "error":
                print(f"ошибка: {message.get('message')}")
                return 1
    print("ответа не дождался")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
