# Voice Shell for Claude Code

Talk to a running Claude Code session from your pocket: the phone stays in your
pocket, the screen stays off, and the answer arrives in your ear.

[Русский](README.ru.md) · [Español](README.es.md) · [中文](README.zh.md)

```
you
  ↕ speech
Android app
  ↕ WebSocket (LAN / Tailscale / HTTPS)
daemon
  ↕
one long-lived Claude Code session
  ↕
repository / shell / git / tests
```

It is not another agent. It is voice transport, routing and a presentation
layer over a session that already works.

---

## Why this and not the things that exist

Claude Code already has a voice mode: you hold space at the keyboard and it
types what you say. That is dictation — you are still at the machine, still
reading the screen.

Claude Code Remote Control and the Cursor mobile app put the session on your
phone's screen. That is a remote control — you are still looking at something.

Here nothing is on screen. A wake word runs on the phone itself, so the
microphone can stay open without audio leaving the device. You say "Клод", wait
for a tone, and speak. Claude works in the repository and reads the result back
in one to three sentences. You can cut it off mid-answer. You can hand it a job
and walk away — background tasks run on their own git worktree and report back
when the ear is quiet.

The price of that is narrow scope. Read [Limits](#limits) before you install.

---

## What you need

| | |
|---|---|
| A machine that stays up | A VPS, or your own computer at home. The daemon has been run on Linux; that is all it has been tested on. |
| Claude access | A Claude subscription (Pro or Max) if you are running this for yourself, or an Anthropic API key. See [Which credential](#which-credential). |
| An Android phone | The app is Android only. There is no iOS app, and iOS sits in the last stage of the roadmap. |
| Bluetooth headphones | Not required, but the whole point. The app raises the headset's own microphone so the phone is not listening through fabric. |

A browser client also exists, for trying this without installing anything. It
is weaker: no wake word, no always-on "stop". See [`docs/DEPLOY.md`](docs/DEPLOY.md).

### Which credential

A Claude Pro or Max subscription works when **you** run this on **your own**
machine for **yourself**. Since February 2026 Anthropic no longer permits
subscription OAuth credentials to be used from third-party products, so you
cannot stand this up as a service and let other people talk to it on your
subscription. If anyone other than you is going to use an instance, that
instance needs its own Anthropic API key.

The question this raised, and the letter written about it, are in
[`docs/anthropic-inquiry.md`](docs/anthropic-inquiry.md).

---

## Install

### The server

Over ssh, from a phone if that is what you have:

```bash
# look first, change nothing
curl -fsSL https://raw.githubusercontent.com/aisarus/Ccvoice-/claude/voice-shell-claude-code-77wwh2/scripts/install-server.sh | CHECK=1 bash

# then install
curl -fsSL https://raw.githubusercontent.com/aisarus/Ccvoice-/claude/voice-shell-claude-code-77wwh2/scripts/install-server.sh | sudo bash
```

The script installs Node, the Claude Code CLI and the Python dependencies, runs
the test suite, writes a systemd unit, and prints an address and a token. It
stops at the tests: if they fail, the service does not come up.

Point a domain at the server and it adds HTTPS through Caddy:

```bash
curl -fsSL https://raw.githubusercontent.com/aisarus/Ccvoice-/claude/voice-shell-claude-code-77wwh2/scripts/install-server.sh | sudo DOMAIN=voice.example.com bash
```

Without a domain the port is open and the traffic is plain HTTP. The token
keeps strangers out; it does not encrypt the conversation. Use a domain, or
Tailscale with the port firewalled.

Two commands run the server after that:

```bash
sudo bash /opt/voice-shell/scripts/update-server.sh   # fetch + reset, restart
sudo bash /opt/voice-shell/scripts/doctor.sh --fast   # one report on everything
```

Updating is not `git pull`. On diverged branches `pull` stops and asks how to
merge; there is nothing to merge here, the branch is the truth.
`update-server.sh` does fetch + reset, prints what is about to disappear, and
leaves a `before-update-…` tag to come back to.

### The daemon on your own computer

A cloud box cannot see your machine. To let Claude Code work on real projects,
run the daemon where the projects are:

```bash
curl -fsSL https://raw.githubusercontent.com/aisarus/Ccvoice-/claude/voice-shell-claude-code-77wwh2/scripts/install-desktop.sh | bash -s -- ~/your-project
```

It clones the repository, installs dependencies, runs the tests, generates a
token and starts the daemon. Reach it from outside the house over Tailscale.

If a Claude Code session is sitting at that computer, hand it
[`docs/desktop-handoff.json`](docs/desktop-handoff.json) instead: the same
steps, machine-readable, including what to check and what to tell you.

### The app

[Download the APK](https://github.com/aisarus/Ccvoice-/releases/download/apk-latest/app-debug.apk)
— rebuilt on every push to the working branch. Grant microphone and
notification permissions: without notifications Android will not let a
foreground service run at all. Then three fields, once: address, token,
starting language.

> **The released APK is signed with a debug key that lives in this repository,
> password and all** — the keystore is `android/app/voice-shell.keystore` and
> the password is in plain text in `android/app/build.gradle.kts`. It is there
> on purpose: otherwise every CI build would get a fresh random key and updates
> would not install over each other. The consequence is real. Anyone can build
> an APK signed with that same key, and Android will accept it as an update to
> yours. **Install the APK only from this repository's releases.** If you build
> your own, generate your own keystore and keep it out of the repository.

---

## How to talk

Two beats. This is the part people get wrong:

> "Клод" · pause · wait for the tone · "run the tests"

The wake word is heard by a small model on the phone (Vosk, ~45 MB, downloaded
on first launch). Its only job is "Клод" and "stop". The utterance itself goes
to the phone's own recognizer, which is far better but does not start
instantly. Said in one breath it still works, worse: after 3.5 seconds the app
stops waiting and sends whatever the small model heard.

The tone after "Клод" is permission to speak. Speak before it and half the
sentence is gone.

| Sound | Meaning |
|---|---|
| short beep + vibration | microphone open, talk |
| soft click + short vibration | utterance accepted, sent |
| low beep | heard nothing, microphone closed empty |

The wake word tolerates one letter: "клот", "клад", "плод" all count. "Код",
"чат", "кот", "что", "как" never count — those are how commands start. Saying
it during an answer is an interrupt: Claude stops talking and listens.

Every utterance goes to one of three targets, and the daemon picks unless you
name one:

| Target | What it is | Changes the project |
|---|---|---|
| `код` | Claude Code in the working directory | yes |
| `чат` | a separate conversation with no project access; web search and page reading only | no |
| `заметка` | a line appended to an inbox file | no |

Ambiguous goes to `чат` on purpose: a wrong `чат` costs a sentence, a wrong
`код` has already done something.

The shell also handles undo, memory, background tasks, a Telegram bridge and
voice approvals by itself, without waking Claude. Every phrase it knows is in
[`docs/VOICE.md`](docs/VOICE.md).

### The command phrases are Russian

This matters more than anything else on this page. The wake word answers to
both "Клод" and "claude", the utterance is recognized in Russian, English or
Hebrew, and Claude answers in the language you used. But the phrases the
*shell* understands — routing prefixes, undo, memory, background tasks, the
Telegram bridge, yes/no on a permission prompt — exist in Russian only. In
English only "stop", "quiet", "enough", "shut up", "stop working", "abort",
"cancel" and the language switch are wired up.

Spoken to in English, this is a voice pipe to Claude Code and not much more.
Nobody has written the phrase tables for the other languages yet.

---

## Optional pieces

**GitHub.** The `код` target runs through a real shell, so GitHub works through
`gh` once it has a token:

```bash
sudo bash /opt/voice-shell/scripts/setup-github.sh ghp_YOUR_TOKEN "Your Name" you@example.com
```

Needs root and a running service — it writes to `/etc/voice-shell.env`. Token:
[github.com/settings/tokens](https://github.com/settings/tokens) → classic →
`repo` and `workflow`. A bad token is rejected on the spot rather than saved as
a surprise for later.

**Telegram.** One preset chat, so a file can leave the machine by voice without
a stranger's voice being able to redirect it:

```bash
sudo bash /opt/voice-shell/scripts/setup-telegram.sh
```

It asks you for a bot token from @BotFather, finds your chat id from the first
message you send the bot, and sends a test message there.

**Voice approvals.** Off by default: `PERMISSION_MODE` is `auto` and nothing is
ever asked. `guarded` asks before destructive things, `ask` asks before
everything but safe reads.

---

## Run it locally

```bash
pip3 install -r daemon/requirements-dev.txt
cd daemon && python3 -m voice_claude --workspace ~/your-project
```

Client and WebSocket share one port (`8787` or `$PORT`); the daemon prints a
token at startup. Over plain `http://` the browser gives a microphone to
`localhost` only.

With no credential connected, `code` and `chat` answer with a stub. The loop
still runs end to end and you hear an honest "Claude unavailable: …" with the
reason, rather than an invented answer.

```bash
python3 -m pytest tests -q        # 250 passed
python3 scripts/validate_spec.py  # OK: voice-shell-for-claude-code v0.2.0 (29 top-level sections)
```

The validator runs without dependencies, checking cross-references only;
`pip install jsonschema` adds schema validation.

---

## Limits

**Not built:**

- **On-device VAD endpointing.** The app decides you have finished by timer,
  not by hearing you stop.
- **Voiceprint.** The speaker classifier carries a `with_voiceprint` profile
  and weights for it, but the app never measures voiceprint similarity, so that
  profile never runs. Speaker roles are decided on acoustics alone.
- **Multi-project.** One workspace per daemon, no switching sessions by voice.
- **Server-side speech recognition.** Recognition happens on the phone, or, in
  the browser client, at Google. The daemon never sees audio.
- **Proactive ambient suggestions.** Ambient `passive` works: the buffer holds
  ten minutes and answers recall questions. The `assist` triggers are defined
  in the spec and never fired.
- **iOS.** No.

**Built but untuned:**

- **Speaker identification** is implemented and tested against synthetic
  profiles. It has never been calibrated on real recordings, so the thresholds
  are guesses. Expect your own quiet speech to come back `unknown` from a cold
  start.

**Sharp edges:**

- `откати` on its own is an undo phrase. Shell commands are matched at the
  start of an utterance, after fillers like "клод", "давай", "ну", so ordinary
  conversation about rolling back is safe — but an utterance that opens with
  "откати" performs a real `git reset --hard` to the previous checkpoint. The
  reverted commit stays in git history.
- Background tasks need the workspace to be a git repository. At most two run
  at once.
- Telegram refuses anything matching `.env`, keys, keystores or names
  containing token/secret/password, anything outside the working directory, and
  anything over 45 MB. It refuses out loud, with the reason.
- The daemon under systemd usually runs as root. The Claude CLI refuses to run
  as root with permissions disabled, so that behaviour is supplied through a
  callback instead.
- A checkpoint is written per utterance, not per file. Undo takes back
  everything one utterance changed.

---

## What is in this repository

| Path | What it is |
|---|---|
| [`spec/voice-shell.json`](spec/voice-shell.json) | The full spec, machine-readable — the single source of truth |
| [`spec/voice-shell.schema.json`](spec/voice-shell.schema.json) | JSON Schema (draft 2020-12) for the spec |
| [`scripts/validate_spec.py`](scripts/validate_spec.py) | Validator: schema plus cross-reference consistency checks |
| [`daemon/`](daemon/) | `voice-claude-daemon`: speaker roles, routing, voice formatting, WebSocket |
| [`android/`](android/) | The app: on-device wake word, background service, headset button |
| [`client/web/`](client/web/index.html) | Chrome-on-Android client: push-to-talk, STT, TTS, earcons |
| [`tests/`](tests/) | 250 tests, including end-to-end over the real protocol |
| [`Dockerfile`](Dockerfile) · [`render.yaml`](render.yaml) | One-click deploy, from a phone |
| [`docs/VOICE.md`](docs/VOICE.md) | Every phrase the system understands by itself |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Symptom → what to check → how to fix it |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Step-by-step deploy with no computer |
| [`docs/desktop-handoff.json`](docs/desktop-handoff.json) | Machine-readable brief for a Claude Code session on your PC |

---

## Design decisions

- **One physical action: `ACTIVATE`.** No tap/double-tap/swipe vocabulary —
  that turns the thing into a TV remote. Everything else is speech and context.
- **The headset button is the backup channel.** Intended split: ~90% wake word,
  ~9% the dialogue window carrying on by itself, ~1% button.
- **A 15-second dialogue window.** After an answer, no wake word needed.
- **Your speech is never rewritten.** Claude's output is retold in one to three
  sentences; the full output stays on screen.
- **Barge-in is split.** "Стоп" stops the voice only. "Останови работу"
  interrupts Claude Code.
- **Route by meaning, not by code words.** A keyword table settles the obvious
  instantly; on live speech a fast model picks the target within 2.5 seconds or
  not at all. A spoken prefix, a chip in the UI or a previous correction is
  already the human's decision and is never second-guessed.
- **One long-lived session**, not a fresh Claude per request.
- **No cloud of our own.** LAN at home, Tailscale outside.

### Who is speaking

The app decides a role for every speech segment and sends it to Claude as a
service line ahead of the transcript:

```
[voice-shell] speaker=master (говорит мастер) confidence=0.93 device=phone_mic

Fix the bug and run the tests.
```

Loudness is the main signal but not the only one: a nearby stranger's voice, or
your own quiet remark, would break a classifier built on level alone. The
decision is weighted logistic regression over relative loudness, SNR,
direct-to-reverberant ratio, C50, high-frequency content, proximity effect, an
optional local voiceprint and device/continuity priors.

| Role | Meaning | Allowed |
|---|---|---|
| `master` | close, clean speech from the owner | everything: wake, commands, barge-in, approvals |
| `bystander` | someone else, a TV, the next room | nothing; not sent at all by default |
| `unknown` | not confident enough | nothing runs, one short re-ask |
| `self_echo` | our own TTS in the microphone | discarded |

Low confidence produces `unknown` rather than a guess. Approvals are accepted
only from `master` at confidence ≥ 0.85.

### The second ear (ambient)

Off by default. Three submodes: `off`, `passive` (a local ten-minute ring
transcript that leaves the device only when you ask it something) and `assist`
(transcript streamed to the chat target).

Raw audio is never stored, the buffer is wiped on leaving the mode, and other
people's speech does not enter it without a separate opt-in. Recording other
people is regulated differently in different jurisdictions, which is why this
is a setting with a private default rather than an implementation detail.

Answers in ambient are `whisper_output`: one sentence, twelve words maximum,
−6 dB, only in a silence gap of 1.2 s or longer, never over your own speech.
The wake word is off in ambient — saying "Клод" in front of people is exactly
what this mode avoids.

---

## Translations

[Русский](README.ru.md) · [Español](README.es.md) · [中文](README.zh.md)

Documentation: [`docs/VOICE.md`](docs/VOICE.md) ·
[`docs/ГОЛОС.md`](docs/ГОЛОС.md) (ru) ·
[`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) ·
[`docs/ЕСЛИ-НЕ-РАБОТАЕТ.md`](docs/ЕСЛИ-НЕ-РАБОТАЕТ.md) (ru) ·
[`docs/DEPLOY.md`](docs/DEPLOY.md) ·
[`docs/DEPLOY.ru.md`](docs/DEPLOY.ru.md) (ru)
