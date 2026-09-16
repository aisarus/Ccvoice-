# voice-claude-daemon

Демон на ПК: держит одну долгую Claude Code-сессию, маршрутизирует реплики
между `code` / `chat` / `note`, классифицирует говорящего и отдаёт телефону
состояние, короткие сводки и запросы разрешений.

## Запуск

```bash
pip install -r daemon/requirements.txt
python -m voice_claude --workspace ~/aegis          # из каталога daemon/
```

Демон печатает токен и адрес клиента:

```
voice-claude-daemon
  workspace : /home/you/aegis
  websocket : ws://0.0.0.0:8787
  client    : http://<этот-хост>:8788/?port=8787&token=XXXX
  token     : XXXX
  code      : ready
  chat      : ready
```

Открой этот адрес в Chrome на Android (в домашней сети — по локальному IP,
снаружи — по имени хоста в Tailscale). Держи кнопку, говори, отпускай.

Без `claude-agent-sdk` / `ANTHROPIC_API_KEY` цели `code` и `chat` работают
заглушками: петля целиком проходит, а в ухо приходит «Claude Code недоступен».
Это сделано нарочно, чтобы отлаживать голосовой тракт отдельно от ключей.

## Флаги

| Флаг | Смысл |
|---|---|
| `--workspace` | каталог Claude Code-сессии |
| `--ws-port` / `--http-port` | порты WebSocket и статики (8787 / 8788) |
| `--token` | фиксированный pre-shared token вместо случайного |
| `--ambient off\|passive\|assist` | стартовый режим «второго уха» |
| `--note-path` | файл инбокса для цели `note` |

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
