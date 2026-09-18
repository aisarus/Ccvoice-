# What to say

Every phrase the system handles by itself. Everything else goes to Claude as an
ordinary utterance — you can talk to him like a person.

**This page is Russian and English.** Those are the two languages the shell is
documented in, and the two the tables below spell out.

Before the tables, the two behaviours that decide how they are used:

- **What the shell hears is not configured at all.** Command phrases from every
  language it knows are matched at the same time, because the shell cannot know
  which language your next sentence will be in, and asking you to flip a switch
  before speaking would defeat a hands-free interface. That is safe because
  these are commands, not prose: the tables are short, specific, and chosen not
  to collide across languages. "undo" and «откати» are both live in the same
  utterance slot, and you never say which one you are about to use.
- **What the shell says is decided per utterance**, in this order: the language
  the phone declared when it connected (`hello.language`), then the language you
  actually just spoke, then `VOICE_LANG`, then English. Switch to English in the
  middle of a Russian conversation and the next answer is in English, with no
  settings to visit. The long-lived chat, summariser and routing sessions are
  raised again when the language changes, and the Claude Code session is told
  the language on every utterance — otherwise the answer slides back to the
  language the session was started in.

**Spanish and Chinese exist in the code and are not written out here.**
`daemon/voice_claude/lexicon.py` carries `es` and `zh` columns for every table on
this page, `i18n.py` carries everything the shell says in those two as well, and
the tests cover them. They are simply not the focus: they were translated
carefully but **have not been reviewed by native speakers**, and this page is not
the place to learn them from — read
[`lexicon.py`](../daemon/voice_claude/lexicon.py), which is short. **Hebrew is a
recognition language only**: there are no phrase tables for it at all, so spoken
Hebrew is a voice pipe to Claude and nothing the shell parses itself.

**How to read the tables.** Each command gets one row per language, tagged
**EN** and **RU**. Say any phrase in the row and it works; you do not have to
say the first one. Every phrase here is copied out of the code, not written from
memory: the command tables are
[`lexicon.py`](../daemon/voice_claude/lexicon.py), the targets and the
permission answers are `targets.router` and `permissions.answers` in
[`spec/voice-shell.json`](../spec/voice-shell.json), wake word and "stop" are
[`Intents.kt`](../android/app/src/main/java/com/voiceshell/Intents.kt), undo and
memory are [`checkpoints.py`](../daemon/voice_claude/checkpoints.py) and
[`memory.py`](../daemon/voice_claude/memory.py), background tasks are
[`tasks.py`](../daemon/voice_claude/tasks.py), the Telegram bridge is
[`telegram.py`](../daemon/voice_claude/telegram.py), and everything the shell
says back is [`i18n.py`](../daemon/voice_claude/i18n.py).

Russian original: [`ГОЛОС.md`](ГОЛОС.md).

---

## The main thing: speak in two beats

> "Claude" · pause · wait for the tone · "show me the server logs"

The wake word is the name, in whichever of the two you are speaking:

| Lang | Say |
|---|---|
| **EN** | "claude" |
| **RU** | «клод» · «клауд» · «клоуд» |

It is heard by a small local model (Vosk, 45 MB). Its job is the name and
"stop", nothing else. The utterance itself is transcribed by the phone's own
recognizer, which is far better but does not start instantly.

Said in one breath it still works, worse. The good recognizer comes up when
there is nothing left to say; after 3.5 seconds the app stops waiting for it and
sends what the small model heard. That is where "he misheard me" comes from.

The tone after the wake word is permission to speak. Speak before it and half
the utterance is gone.

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

### Filler in front of a command is ignored

A command has to open the utterance, but throat-clearing before it does not
count. These words are stripped first, in both languages at once:

| Lang | Say |
|---|---|
| **EN** | "claude" · "hey" · "ok" · "okay" · "listen" · "so" · "well" · "please" · "yo" |
| **RU** | «клод» · «клауд» · «слушай» · «эй» · «окей» · «ок» · «а» · «ну» · «и» · «так» · «давай» · «пожалуйста» |

---

## Where the utterance goes

Three targets. The daemon picks by itself, but you can name the choice out loud,
and then it does not guess.

| Target | What it does | Changes the project |
|---|---|---|
| `code` | Claude Code in the working directory | yes |
| `chat` | a separate conversation with no project access; it has web search and page reading, and nothing that writes | no |
| `note` | a line in an inbox file | no |

**The named target is the first word of the utterance.** In the middle of a
sentence the prefix does nothing.

| Lang | To `code` | To `chat` | To `note` |
|---|---|---|---|
| **EN** | "code …" · "in code …" · "claude code …" · "in the project …" | "chat …" · "in chat …" · "question …" · "just tell me …" | "note …" · "note that …" · "write down …" · "take a note …" |
| **RU** | «код …» · «в код …» · «клод код …» · «в проекте …» | «чат …» · «в чат …» · «вопрос …» · «просто скажи …» | «запиши …» · «заметка …» |

Whole examples, wake word included:

> "Claude" · tone · "in the project run the tests"
> «Клод» · сигнал · «в чат что такое вектор эмбеддинга»
> "Claude" · tone · "note down the idea about the second ear"

If no target is named: for 15 seconds after an answer the previous target
holds, then a keyword table decides, and on unfamiliar words a fast model does
(up to 2.5 s). If nobody decided, it goes to `chat`. That is deliberate: chat
changes nothing, and a mistaken `code` has already executed something.

The keyword table mostly does not need translating: "git", "npm", "pytest",
"docker", "gradle", "eslint" are the same words in any language, so they are
stored once. Around them sit the words that do differ — "test" and «тест»,
"branch" and «ветка», "file" and «файл» — and those live per language in
`CODE_STEMS` and `CODE_WORDS` in
[`lexicon.py`](../daemon/voice_claude/lexicon.py).

Phrases that lean the other way, towards `chat`:

| Lang | Say |
|---|---|
| **EN** | "what is" · "what's a" · "who is" · "explain" · "work out" · "calculate" · "translate" · "what do you think" · "how much is" · "remind me" · "what time" · "is it worth" · "what's the difference" · "write an email" |
| **RU** | «что такое» · «кто такой» · «объясни» · «посчитай» · «сформулируй» · «напиши письмо» · «как думаешь» · «переведи» · «что он сказал» · «что она сказала» · «напомни» · «во сколько» · «сколько будет» · «какая разница» · «стоит ли» |

And the phrases that mean "carry on with what we were doing", which keep the
previous target instead of starting a new routing decision:

| Lang | Say |
|---|---|
| **EN** | "go on" · "keep going" · "carry on" · "continue" · "finish it" · "go ahead" · "next" |
| **RU** | «продолжай» · «добей» · «давай» · «дальше» · «ок делай» · «окей делай» |

### It went to the wrong place

| Lang | Say |
|---|---|
| **EN** | "wrong target" · "not the code" · "i didn't mean code" · "that's not what i meant" |
| **RU** | «не туда» · «я не про код» |

Resends the **previous** utterance to the other target and remembers the
correction: next time a similar phrase goes to the right place immediately. The
memory lives in `.voice-shell/routes.json` and survives a restart.

The target can be named outright: "wrong target, in chat", «не туда, в чат».

The correction can be said anywhere in the utterance — it is searched for across
the whole thing, not only at the start.

### Moving the conversation across targets

| Lang | To `code`, carrying the last `chat` answer | To `chat`, carrying the last `code` output |
|---|---|---|
| **EN** | "hand this to code" · "do this in the project" · "make it so" | "explain it simply" · "what does that mean" · "put that in plain words" |
| **RU** | «перекинь это в код» · «сделай это в проекте» | «объясни попроще» · «а что это значит» |

The handoff is checked before routing, so these phrases are never sent to the
keyword table or the model. If there is nothing to carry over yet, the shell
says so — "Nothing to hand over yet.", in the language you asked in.

---

## Background tasks: say it and forget it

An ordinary utterance is a conversation: you wait for the answer. A background
task is work he does while you are busy with something else. What makes a task
background is not how long it takes — it is your decision not to wait.

**Put it in the background** — "Started on it: … I'll tell you when it's done."
at once, the result whenever it is ready:

| Lang | Say |
|---|---|
| **EN** | "in the background" · "in background" · "do it later" · "later on" · "queue it up" · "add a task" · "on the side" · "when you have time" |
| **RU** | «в фоне» · «фоном» · «займись» · «потом сделай» · «сделай потом» · «поставь в очередь» · «добавь задачу» · «на потом» |

**What is running** — one sentence about what is being done:

| Lang | Say |
|---|---|
| **EN** | "what are you doing" · "what are you working on" · "what's in progress" · "what is in progress" · "what's running" · "task status" · "what's in the queue" |
| **RU** | «чем занят» · «чем занимаешься» · «что в работе» · «что делаешь сейчас» · «какие задачи» · «что в очереди» · «статус задач» |

**What is finished** — the last finished task and its result:

| Lang | Say |
|---|---|
| **EN** | "what's done" · "what is done" · "what's finished" · "show what's finished" · "what did you finish" · "which tasks are done" |
| **RU** | «что готово» · «покажи готовое» · «что доделал» · «какие задачи готовы» |

**Drop one** — finds it by the words that follow and cancels it, as in "cancel
the task about dependencies":

| Lang | Say |
|---|---|
| **EN** | "cancel the task" · "drop the task" · "remove the task" · "don't do the task" · "forget the task" |
| **RU** | «отмени задачу» · «брось задачу» · «убери задачу» · «не делай задачу» |

These are matched anywhere in the utterance, not only at the start.

How it works and why:

- **Every task gets its own copy of the project** (a git worktree) and its own
  branch, `voice/<number>-<start of the phrase>`. Two tasks do not fight over
  the same files, and each has its own undo. Your working copy is not touched
  at all.
- **Background tasks need a git repository.** Outside one the shell answers
  "Background tasks only work inside a repository." and does nothing.
- **No more than two run at once**: this is your machine and your subscription.
  The rest wait in the queue.
- **A finished task is not shouted over a conversation.** It waits for silence —
  when you are not talking and he is not answering — and only then reports.
- **A service restart does not lose what you said**: the list is in
  `.voice-shell/tasks.json`. A task caught mid-run by a restart honestly becomes
  "interrupted when the service restarted".
- **He will not merge a task branch into yours.** To look at the result:
  `git -C <project> log voice/<number>-…`

## A file to Telegram

| Say | What happens |
|---|---|
| "send it to telegram", «скинь мне в телегу» | sends what Claude was changing a moment ago |
| "send the config to telegram", «скинь в телегу конфиг» | finds a file by how the name sounds and sends it |

A fixed list of sentences does not survive real speech, so two words have to be
there — an action and an addressee, in any order:

| Lang | Action | Addressee |
|---|---|---|
| **EN** | send · share · drop · forward · push | telegram |
| **RU** | скинь/скини/скинуть · отправь/отправи/отправить · пришли · кинь/кини · перешли · шли | телега · телеграм · тг |

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

| Lang | Say |
|---|---|
| **EN** | "undo the last thing" · "undo the last" · "undo that" · "undo" · "roll that back" · "roll it back" · "roll back" · "revert the last" · "revert that" · "put it back" · "undo the changes" · "take that back" |
| **RU** | «откати последнее» · «откати» · «отмени последнее» · «отмени изменения» · «верни как было» · «верни обратно» · «отмена последнего» |

Every utterance that changed something in the project is closed with a commit.
Undo restores the state before it and says what it rolled back: "Rolled back
auth.ts and config.json. You had said: …".

Nothing is lost: the reverted commit stays in git history.

**The command has to be at the start of the utterance.** The filler listed
[above](#filler-in-front-of-a-command-is-ignored) is stripped first, but a
command in the middle is not a command. «А это можно откатить?» — "can that be
rolled back, actually?" — is a question and stays a question.

**While work is running, undo waits.** `git reset --hard` under Claude's hands
would bring back a mixture, so the shell interrupts the session and waits for
the turn to wind down. If it does not, you hear "Work is still running, undoing
now is risky. Say «stop working»."

Note the collision with "stop": bare «отмени» and bare «отмена» are caught by
the phone as "stop the work". With anything after them — «отмени последнее»,
«отмени изменения» — they reach the daemon as undo.

### What was done

| Lang | Say |
|---|---|
| **EN** | "what did you do" · "what have you done" · "what did you change" · "show the last changes" · "show recent changes" · "what changed" · "what has changed" |
| **RU** | «что ты сделал» · «что ты наделал» · «покажи последние изменения» · «какие были изменения» · «что изменилось» |

The last three checkpoints, by what was said to produce them.

### Memory

A plain text file, `.voice-shell/memory.md`, slipped into every utterance. You
can open it and fix it by hand.

**Remember this** — "Got it.":

| Lang | Say |
|---|---|
| **EN** | "remember that" · "remember" · "keep in mind that" · "keep in mind" · "don't forget that" · "don't forget" · "note that" · "for the future" |
| **RU** | «запомни что» · «запомни» · «имей в виду что» · «имей в виду» · «на будущее» · «не забудь что» · «не забудь» |

**What do you remember** — names the last five facts:

| Lang | Say |
|---|---|
| **EN** | "what do you remember about me" · "what do you remember" · "show your memory" · "what's in your memory" · "what is in your memory" |
| **RU** | «что ты обо мне помнишь» · «что ты помнишь» · «покажи память» · «что у тебя в памяти» |

**Drop a fact** — removes the facts that mention the word that follows:

| Lang | Say |
|---|---|
| **EN** | "forget about" · "forget that" · "forget" |
| **RU** | «забудь про» · «забудь что» · «забудь» |

The command has to be at the start of the utterance: "remember that …" works,
"and do remember …" does not.

Forgetting matches Russian case endings, not literal strings: «забудь про ночи»
does remove the fact «я работаю по ночам». It compares the shared stem and
requires both tails to look like endings, so «забудь про проектор» will not take
away a fact about «проект». That rule is Russian morphology and does not
generalise; elsewhere the comparison is the plain one.

Two things that sound alike do different things: "note that" as a target prefix
puts a line in the inbox, "remember that" puts it in memory. In Russian the
collision is audible — «запиши» against «запомни».

---

## Stop

"Stop" is heard by the phone, not the server: stopping must not depend on a
round trip. So it works always — before the wake word, during an answer and
during work.

| Lang | Stops the voice only — Claude goes quiet, the work carries on | Stops the work in Claude Code |
|---|---|---|
| **EN** | "stop" · "quiet" · "enough" · "shut up" | "stop working" · "stop the work" · "abort" · "cancel" |
| **RU** | «стоп» · «тихо» · «хватит» · «замолчи» | «останови работу» · «стоп работу» · «стоп работа» · «останови» · «прекрати» · «отмени работу» · «отмена работы» |

«отмени» and «отмена» said alone, with nothing after them, stop the work. With
anything after them they are an undo and go to the daemon instead.

The wake word before a stop word is not needed, and does not get in the way:
"Claude, stop" works too.

---

## Language

You do not have to set the language of the conversation. The shell listens in
every language it knows at once and, by default, answers in the one it heard.
Pinning the answers to one language is possible, and is the only thing here you
ever have to touch.

**What it answers in**, in order — the first one that gives an answer wins:

1. a language you pinned by voice, if you pinned one — see below;
2. the language of the utterance it just heard — Cyrillic script means Russian,
   CJK means Chinese, and Latin script is decided by a short list of common
   words, so a wrong guess costs one sentence and the next utterance re-decides;
3. the language the phone declared when it connected (`hello.language` in the
   protocol; the daemon reports what it settled on back in `welcome.language`);
4. `VOICE_LANG` in the environment;
5. English.

So switching language mid-conversation needs no settings change at all: say the
next sentence in English and the answer comes back in English. The chat,
summariser and routing sessions are raised again when the language changes, and
the Claude Code session is reminded of the language on every utterance, so the
answer does not slide back to the language the session started in.

**To pin the answers to one language**, say so:

| Lang | Pin the answers | Go back to answering in whatever you speak |
|---|---|---|
| **EN** | "english" · "russian" | "as i asked" · "same language" · "auto" · "automatic" |
| **RU** | «английский» · «по английски» · «инглиш» · «русский» · «по русски» · «рашн» | «как спросил» · «как спрошу» · «как я сказал» · «любой язык» · «автоматически» |

Said after the wake word — "Claude, English", «Клод, как спросил». «Переключись
на …», «говори …», `switch to …` and `speak …` in front are stripped, and
Spanish, Chinese and Hebrew are on the same switch; the exact words per language
are in
[`Intents.kt`](../android/app/src/main/java/com/voiceshell/Intents.kt). It
answers in the language it has just been moved to — "Answering in English.",
«Отвечаю по-русски.», or «Отвечаю на языке вопроса.» when you unpin it.

A pinned language beats everything else in the list above, including the
language you are actually speaking. That is the point of it: you can speak
Russian and be answered in English.

**Pinning the answers does not move the microphone.** What the phone's own
recognizer listens for is a separate thing, with its own command: dragging it
along behind the answer would leave the shell deaf to the language you are
actually speaking. By default the app asks Android to recognise every language
it knows, starting with the one picked on its screen. That is best-effort — it
is the recognition service that honours the request, and one that does not
listens in the picked language alone.

| Lang | Move the microphone | One language only | Every language again |
|---|---|---|---|
| **EN** | "listen in …" · "listen to …" · "listen …" · "recognise …" · "recognize …" | "listen only …" · "listen to one language" · "one language only" | "listen to all languages" · "listen in any language" · "understand every language" |
| **RU** | «слушай …» · «слушай по …» · «слушай на …» · «распознавай …» | «слушай только …» · «слушай один язык» · «только один язык» | «слушай все языки» · «слушай любой язык» · «понимай все языки» |

So the three are told apart by one word: "Claude, English" moves the answers,
"Claude, listen in English" moves the microphone, and "Claude, listen only in
English" does both at once. The scope is said back — "Listening in every
language I know." or "Listening in one language only." — and the picked language
can still be changed on the app's screen instead.

None of this touches understanding: the shell's own command tables are matched
in every language whatever the microphone or the answer is doing.

**The default** — what it answers in when nothing above it settled the question:

```bash
echo 'VOICE_LANG=en' | sudo tee -a /etc/voice-shell.env
sudo systemctl restart voice-shell
```

`VOICE_LANG` takes `en`, `ru`, `es` or `zh`. It is the last resort, not an
override: a pinned language, an utterance whose language is recognised, and the
language the phone declared all win over it.

Answers coming back in a language you did not expect are covered in
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md#it-answers-in-a-language-you-did-not-expect).

---

## Approvals by voice

Only works if `PERMISSION_MODE=guarded` or `PERMISSION_MODE=ask` is set in the
environment. **The default is `auto`: nothing is ever asked.**

When the mode is on and Claude asks ("Claude wants to push the changes.
Allow?"), your utterance is an answer to that question, not a new command:

| Lang | Allow once | Refuse |
|---|---|---|
| **EN** | "yes" · "ok" · "okay" · "go ahead" · "allow" · "do it" | "no" · "don't" · "do not" · "cancel" · "stop" |
| **RU** | «да» · «давай» · «разрешаю» · «ок» | «нет» · «не надо» · «отмена» |

| Lang | Allow and remember | Read the command out in full |
|---|---|---|
| **EN** | "yes and stop asking for this command" · "always allow this" | "what exactly" · "what exactly?" · "details" · "tell me more" |
| **RU** | «да, и больше не спрашивай для этой команды» · «всегда разрешай это» | «что именно?» · «подробнее» |

The longest match wins, so «да, и больше не спрашивай» does not collapse into a
plain «да», and "yes and stop asking for this command" does not collapse into
"yes".

Not everything can be remembered: `rm -rf`, `git push --force`,
`git reset --hard`, `chmod 777`, `curl … | sh`, `dd`, `mkfs`, `shutdown`,
`reboot`, `kill -9`, `npm publish`, `pip install`, `npm install`, and anything
with `secret`, `token`, `password` or `.env` never enter the allowed list, even
if you say "always allow this". The shell then answers "Allowed once. I won't
remember this one."

An approval is accepted only from the master and only at confidence ≥ 0.85. A
stranger's voice nearby approves nothing.

---

## The second ear

Off by default (`AMBIENT_SUBMODE=passive` turns it on). In `passive` the last
ten minutes of conversation sit in a local buffer and do not go anywhere until
you ask:

> «что он сказал» · «какую цифру он назвал» · «как его зовут»
> «о чём мы договорились» · «повтори последнее» · «что только что было»

**The recall questions are Russian only.** Unlike every other table on this
page, the patterns in [`ambient.py`](../daemon/voice_claude/ambient.py) were
never translated; asked in another language the question is an ordinary
utterance and goes to Claude without the buffer behind it.

The buffer is wiped on leaving the mode. Raw audio is never stored. Other
people's speech does not enter the buffer by default — that needs a separate
opt-in.

---

## What the system does not understand

Things that are in the spec but do not work on the live loop. A wrong hint is
worse than none.

| Phrase from the spec | What actually happens |
|---|---|
| «коди», «компьютер» | written in the spec as wake words, not implemented in the app. There is one wake word, the name itself |

Ambient `assist` triggers are the other case: `proactive_triggers()` exists in
[`ambient.py`](../daemon/voice_claude/ambient.py) and nothing calls it, so
proactive suggestions never fire. The buffer and recall questions do work.

---

## If it does not hear you

Separate page: [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).
