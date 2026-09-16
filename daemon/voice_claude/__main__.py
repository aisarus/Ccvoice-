from __future__ import annotations

import argparse
import asyncio
import logging

from .server import Settings, run
from .spec import defaults


def main() -> int:
    net = defaults("network")
    parser = argparse.ArgumentParser(prog="voice-claude-daemon")
    parser.add_argument("--workspace", default="~", help="каталог Claude Code-сессии")
    parser.add_argument("--ws-port", type=int, default=net["ws_port"])
    parser.add_argument("--http-port", type=int, default=net["ws_port"] + 1)
    parser.add_argument("--token", default=None, help="pre-shared token (по умолчанию случайный)")
    parser.add_argument("--ambient", default=defaults("ambient")["submode"],
                        choices=["off", "passive", "assist"])
    parser.add_argument("--note-path", default=defaults("targets")["note_path"])
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings(workspace=args.workspace, ws_port=args.ws_port, http_port=args.http_port,
                        note_path=args.note_path, ambient_submode=args.ambient)
    if args.token:
        settings.token = args.token
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        print("\nостановлен")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
