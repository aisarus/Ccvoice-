from __future__ import annotations

import argparse
import asyncio
import logging

from .server import Settings, run


def main() -> int:
    env = Settings.from_env()
    parser = argparse.ArgumentParser(prog="voice-claude-daemon")
    parser.add_argument("--workspace", default=None, help="каталог Claude Code-сессии")
    parser.add_argument("--host", default=None,
                        help="адрес привязки; 127.0.0.1 — когда впереди стоит reverse proxy")
    parser.add_argument("--port", type=int, default=None,
                        help="один порт для клиента и WebSocket (по умолчанию $PORT или 8787)")
    parser.add_argument("--token", default=None, help="pre-shared token (по умолчанию $VOICE_TOKEN)")
    parser.add_argument("--ambient", default=None, choices=["off", "passive", "assist"])
    parser.add_argument("--note-path", default=None)
    parser.add_argument("--workspace-repo", default=None, help="git URL, который склонировать на старте")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings(
        workspace=args.workspace or env.workspace,
        host=args.host or env.host,
        port=args.port or env.port,
        token=args.token or env.token,
        note_path=args.note_path or env.note_path,
        ambient_submode=args.ambient or env.ambient_submode,
        workspace_repo=args.workspace_repo or env.workspace_repo,
        router_model=env.router_model,
    )
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        print("\nостановлен")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
