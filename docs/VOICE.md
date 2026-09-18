# What to say

Every phrase the system handles by itself. Everything else goes to Claude as an
ordinary utterance — you can talk to him like a person.

**The shell's own phrases are Russian.** The wake word answers to "Клод" and
"claude", and the utterance after it is recognized in Russian, English or
Hebrew, but the tables below are the phrases the *daemon and the app* parse
themselves, and those exist in Russian only. The exceptions are marked. Nobody
has written the phrase tables for other languages yet.

This list is read out of the code, not out of the plan: wake word and "stop"
from [`Intents.kt`](../android/app/src/main/java/com/voiceshell/Intents.kt),
targets from `targets.router` in [`spec/voice-shell.json`](../spec/voice-shell.json),
undo and memory from [`checkpoints.py`](../daemon/voice_claude/checkpoints.py)
and [`memory.py`](../daemon/voice_claude/memory.py), background tasks from
[`tasks.py`](../daemon/voice_claude/tasks.py), the Telegram bridge from
[`telegram.py`](../daemon/voice_claude/telegram.py).

Russian original: [`ГОЛОС.md`](ГОЛОС.md).

---

## The main thing: speak in two beats

> "Клод" · pause · wait for the tone · "покажи логи сервера"

The wake word is heard by a small local model (Vosk, 45 MB). Its job is "Клод"
and "стоп", nothing else. The utterance itself is transcribed by the phone's own
recognizer, which is far better but does not start instantly.

Said in one breath it still works, worse. The good recognizer comes up when
there is nothing left to say; after 3.5 seconds the app stops waiting for it and
sends what the small model heard. That is where "he misheard me" comes from.

The tone after "Клод" is permission to speak. Speak before it and half the
utterance is gone.

| Sound | What it means |
|---|---|
| short beep + vibration | microphone open, talk |
| soft click + short vibration | utterance accepted and sent |
| low beep | heard nothing, microphone closed empty |

The wake word is matched with a one-letter tolerance: "клот", "клад", "плод"
count as the wake word too. "Код", "чат", "кот", "что", "как" never count —
those are how commands start.

The wake word also works in the middle of an answer. That is the interrupt:
Claude stops talking and listens to the new utterance.

---

## Where the utterance goes

Three targets. The daemon picks by itself, but you can name the choice out loud,
and then it does not guess.

| Target | What it does | Changes the project |
|---|---|---|
| `код` | Claude Code in the working directory | yes |
| `чат` | a separate conversation with no project access; it has web search and page reading, and nothing that writes | no |
| `заметка` | a line in an inbox file | no |

**The named target is the first word of the utterance.** In the middle of a
sentence the prefix does nothing.

| Say | Goes to |
|---|---|
| "код …", "в код …", "в проекте …", "клод код …" | `код` |
| "чат …", "в чат …", "вопрос …", "просто скажи …" | `чат` |
| "запиши …", "заметка …" | `заметка` |

Whole examples, wake word included:

> "Клод" · tone · "в проекте прогони тесты"
> "Клод" · tone · "в чат что такое вектор эмбеддинга"
> "Клод" · tone · "запиши идею про второе ухо"

If no target is named: for 15 seconds after an answer the previous target
holds, then a keyword table decides, and on unfamiliar words a fast model does
(up to 2.5 s). If nobody decided, it goes to `чат`. That is deliberate: chat
changes nothing, and a mistaken `код` has already executed something.

### It went to the wrong place

> "не туда"

Resends the **previous** utterance to the other target and remembers the
correction: next time a similar phrase goes to the right place immediately. The
memory lives in `.voice-shell/routes.json` and survives a restart.

The target can be named outright: "не туда, в чат". "Я не про код" works too.

The correction can be said anywhere in the utterance — it is searched for across
the whole thing, not only at the start.

### Moving the conversation across targets

| Say | What happens |
|---|---|
| "перекинь это в код", "сделай это в проекте" | goes to `код`, carrying the last `чат` answer as context |
| "объясни попроще", "а что это значит" | goes to `чат`, carrying the last `код` output as context |

The handoff is checked before routing, so these phrases are never sent to the
keyword table or the model. If there is nothing to carry over yet, the shell
says so: "Пока нечего перекидывать."

---

## Background tasks: say it and forget it

An ordinary utterance is a conversation: you wait for the answer. A background
task is work he does while you are busy with something else. What makes a task
background is not how long it takes — it is your decision not to wait.

| Say | What happens |
|---|---|
| "в фоне почини падающие тесты" | "Взял в работу…" at once, the result whenever it is ready |
| "фоном обнови зависимости", "займись …", "потом сделай …", "сделай потом …", "поставь в очередь …", "добавь задачу …", "на потом …" | the same |
| "чем занят", "чем занимаешься", "что в работе", "что делаешь сейчас", "какие задачи", "что в очереди", "статус задач" | one sentence about what is being done |
| "что готово", "покажи готовое", "что доделал", "какие задачи готовы" | the last finished task and its result |
| "отмени задачу про зависимости", "брось задачу …", "убери задачу …", "не делай задачу …" | finds it by the words and cancels it |

These are matched anywhere in the utterance, not only at the start.

How it works and why:

- **Every task gets its own copy of the project** (a git worktree) and its own
  branch, `voice/<number>-<start of the phrase>`. Two tasks do not fight over
  the same files, and each has its own undo. Your working copy is not touched
  at all.
- **Background tasks need a git repository.** Outside one the shell answers
  "Фоновые задачи работают только в репозитории" and does nothing.
- **No more than two run at once**: this is your machine and your subscription.
  The rest wait in the queue.
- **A finished task is not shouted over a conversation.** It waits for silence —
  when you are not talking and he is not answering — and only then reports.
- **A service restart does not lose what you said**: the list is in
  `.voice-shell/tasks.json`. A task caught mid-run by a restart honestly becomes
  "встала".
- **He will not merge a task branch into yours.** To look at the result:
  `git -C <project> log voice/<number>-…`

## A file to Telegram

| Say | What happens |
|---|---|
| "скинь мне в телегу" | sends what Claude was changing a moment ago |
| "скинь в телегу конфиг" | finds a file by how the name sounds and sends it |
| "отправь в телеграм", "кинь в телегу", "пришли в телеграм", "перешли в тг" | the same |

Two words have to be there, an action and an addressee, in any order. The action
is any of скинь/скини/скинуть, отправь/отправи/отправить, пришли, кинь/кини,
перешли, шли; the addressee is any of телега/телеграм/тг/telegram in any form.

Set it up once: `sudo bash /opt/voice-shell/scripts/setup-telegram.sh`. It asks
@BotFather for a bot token, works out your chat id from the first message you
send the bot, and sends a test message there.

What matters:

- **There is one addressee and it is set in advance.** It cannot be changed by
  voice: other people may be talking nearby, and a bridge that obeys anyone is a
  way to carry files out of the machine by somebody else's hands.
- **Secrets do not go out.** `.env`, keys, keystores, anything with token,
  secret or password in the name — refused out loud, with the reason. Files
  outside the working directory and over forty-five megabytes — the same.
- **The name is matched by sound**: "конфиг" finds `config.json`, "ридми" finds
  `readme.md`. If several things look alike, or nothing does, it sends nothing
  and says so: the wrong file going out is worse than none.
- **"Скинь мне его"** is not a file called "его". Pronouns mean "the thing we
  were working on": the files from the last checkpoint, or failing that the
  freshest non-test file in the project.

## The shell's own commands

Claude never sees these phrases: the daemon executes them, so they work
instantly and identically even when the session is busy.

### Undo

> "откати последнее"
> "верни как было"

Also: "откати", "отмени последнее", "отмени изменения", "отмена последнего",
"верни обратно".

Every utterance that changed something in the project is closed with a commit.
Undo restores the state before it and says what it rolled back: "Откатил auth.ts
и config.json. Сказано было: …".

Nothing is lost: the reverted commit stays in git history.

**The command has to be at the start of the utterance.** Filler words in front
are ignored — "клод", "слушай", "эй", "окей", "ок", "а", "ну", "и", "так",
"давай", "пожалуйста" — but a command in the middle is not a command. "А это
можно откатить?" is a question and stays a question.

**While work is running, undo waits.** `git reset --hard` under Claude's hands
would bring back a mixture, so the shell interrupts the session and waits for
the turn to wind down. If it does not, you hear "Работа ещё идёт, откатывать
сейчас опасно. Скажи «останови работу»."

Note the collision with "стоп": bare "отмени" and bare "отмена" are caught by
the phone as "stop the work". With anything after them — "отмени последнее",
"отмени изменения" — they reach the daemon as undo.

### What was done

> "что ты сделал"
> "что изменилось"
> "покажи последние изменения"
> "какие были изменения"
> "что ты наделал"

The last three checkpoints, by what was said to produce them.

### Memory

A plain text file, `.voice-shell/memory.md`, slipped into every utterance. You
can open it and fix it by hand.

| Say | What happens |
|---|---|
| "запомни что я работаю по ночам" | "Запомнил." |
| "имей в виду что фронтенд на next" | the same |
| "на будущее …", "не забудь что …" | the same |
| "что ты помнишь", "что ты обо мне помнишь" | names the last five facts |
| "покажи память", "что у тебя в памяти" | the same |
| "забудь про ночи" | removes facts that mention the word |

The command has to be at the start of the utterance: "запомни что …" works, "а
ты запомни …" does not.

Forgetting matches Russian case endings, not literal strings: "забудь про ночи"
does remove the fact "я работаю по ночам". It compares the shared stem and
requires both tails to look like endings, so "забудь про проектор" will not take
away a fact about "проект".

"Запиши" and "запомни" sound alike and do different things: the first puts a
line in the inbox, the second puts it in memory.

---

## Stop

"Стоп" is heard by the phone, not the server: stopping must not depend on a
round trip. So it works always — before the wake word, during an answer and
during work.

| Say | What stops |
|---|---|
| "стоп", "тихо", "хватит", "замолчи" | the voice only: Claude goes quiet, the work carries on |
| "останови работу", "стоп работу", "стоп работа", "останови", "прекрати", "отмени работу", "отмена работы" | the work in Claude Code is interrupted |
| "отмени", "отмена" — said alone, with nothing after | the work |
| `stop`, `quiet`, `enough`, `shut up` | the voice only |
| `stop working`, `stop the work`, `abort`, `cancel` | the work |

The wake word before a stop word is not needed, and does not get in the way:
"Клод, стоп" works too.

---

## Language

> "Клод, английский" · "Клод, русский" · "Клод, иврит"

Also "по-английски", `english`, `инглиш`, `switch to english`, "на иврите",
`hebrew`, `עברית`, "по-русски", `russian`, `рашн`. "Переключись на …", "говори
…", `switch to …` and `speak …` in front are stripped.

The language of the answer needs no separate setting: it is decided by the
script the answer is written in.

---

## Approvals by voice

Only works if `PERMISSION_MODE=guarded` or `PERMISSION_MODE=ask` is set in the
environment. **The default is `auto`: nothing is ever asked.**

When the mode is on and Claude asks ("Клод хочет запушить изменения.
Разрешить?"), your utterance is an answer to that question, not a new command:

| Say | What happens |
|---|---|
| "да", "давай", "разрешаю", "ок" | allow once |
| "нет", "не надо", "отмена" | refuse |
| "да, и больше не спрашивай для этой команды", "всегда разрешай это" | allow and remember |
| "что именно?", "подробнее" | reads the command out in full |

The longest match wins, so "да, и больше не спрашивай" does not collapse into a
plain "да".

Not everything can be remembered: `rm -rf`, `git push --force`,
`git reset --hard`, `chmod 777`, `curl … | sh`, `dd`, `mkfs`, `shutdown`,
`reboot`, `kill -9`, `npm publish`, `pip install`, `npm install`, and anything
with `secret`, `token`, `password` or `.env` never enter the allowed list, even
if you say "всегда разрешай". The shell then answers "Разрешил один раз. Это я
запоминать не буду".

An approval is accepted only from the master and only at confidence ≥ 0.85. A
stranger's voice nearby approves nothing.

---

## The second ear

Off by default (`AMBIENT_SUBMODE=passive` turns it on). In `passive` the last
ten minutes of conversation sit in a local buffer and do not go anywhere until
you ask:

> "что он сказал" · "какую цифру он назвал" · "как его зовут"
> "о чём мы договорились" · "повтори последнее" · "что только что было"

The buffer is wiped on leaving the mode. Raw audio is never stored. Other
people's speech does not enter the buffer by default — that needs a separate
opt-in.

---

## What the system does not understand

Things that are in the spec but do not work on the live loop. A wrong hint is
worse than none.

| Phrase from the spec | What actually happens |
|---|---|
| "коди", "компьютер" | written in the spec as wake words, not implemented in the app. There is one wake word, "Клод" (and "claude") |

Ambient `assist` triggers are the other case: `proactive_triggers()` exists in
[`ambient.py`](../daemon/voice_claude/ambient.py) and nothing calls it, so
proactive suggestions never fire. The buffer and recall questions do work.

---

## If it does not hear you

Separate page: [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).
