# voice-claude-daemon

Демон на ПК: держит одну долгую Claude Code-сессию, маршрутизирует реплики
между `code` / `chat` / `note`, классифицирует говорящего и отдаёт телефону
состояние, короткие сводки и запросы разрешений.

## Запуск

```bash
pip install -r daemon/requirements.txt
python -m voice_claude --workspace ~/aegis          # из каталога daemon/
```

```
voice-claude-daemon
  workspace : /home/you/aegis
  listening : 0.0.0.0:8787 (клиент и WebSocket на одном порту)
  token     : XXXX
  code      : ready
  chat      : ready
```

Клиент — `http://<хост>:8787/?token=XXXX`. Один порт нужен потому, что хостинги
отдают наружу ровно один, а браузеру проще всего открывать `wss://` на том же
origin, что и страница. Микрофон браузер даст только на `https://` или на
`localhost` — для телефона это значит деплой, см. `docs/DEPLOY.md`.

Без `claude-agent-sdk` / `ANTHROPIC_API_KEY` цели `code` и `chat` работают
заглушками: петля целиком проходит, а в ухо приходит «Claude Code недоступен».
Это сделано нарочно, чтобы отлаживать голосовой тракт отдельно от ключей.

## Флаги

| Флаг | Переменная окружения | Смысл |
|---|---|---|
| `--workspace` | `WORKSPACE_DIR` | каталог Claude Code-сессии |
| `--workspace-repo` | `WORKSPACE_REPO` | git URL, который склонировать на старте (+ `GITHUB_TOKEN`) |
| `--port` | `PORT` | один порт для клиента и WebSocket |
| `--token` | `VOICE_TOKEN` | pre-shared token (по умолчанию случайный) |
| `--ambient off\|passive\|assist` | `AMBIENT_SUBMODE` | стартовый режим «второго уха» |
| `--note-path` | `NOTE_PATH` | файл инбокса для цели `note` |

`GET /healthz` отвечает `ok` — это health check для хостинга.

## Модули

| Файл | Секция спеки |
|---|---|
| `speaker.py` | `speaker_identification` — роли, признаки, пороги, политика |
| `router.py` | `targets.router` — chat / code / note |
| `formatter.py` | `voice_formatter` — вывод в 1–3 предложения, approvals речью |
| `ambient.py` | `ambient_mode` — кольцевой буфер, whisper-гейт, rate limit |
| `state.py` | `states`, `conversation_window` |
| `targets.py` | бэкенды целей (Agent SDK, Messages API, инбокс) |
| `server.py` | `protocol` — WebSocket |

Пороги и веса не продублированы в коде: `spec.py` читает их из
`spec/voice-shell.json`, поэтому валидатор спеки охраняет ровно те значения,
на которых работает демон.
