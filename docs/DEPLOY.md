# Deploy and first test — from a phone, without a computer

All of it happens in a browser on the phone. Nothing to install.

Why deploy at all: a browser will not give microphone access or speech
recognition over `http://`. It needs `https://`, and the easiest place to get
that is a host.

This page is about the **browser** client on Render. The Android app can do
more: there the phone itself hears the wake word, and "стоп" works at any time.
The app and your own server are in the [README](../README.md). Phrases are in
[`VOICE.md`](VOICE.md). Breakage is in
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

Russian original: [`DEPLOY.ru.md`](DEPLOY.ru.md).

---

## What you will need

| What | Where | Why |
|---|---|---|
| A GitHub account | [github.com](https://github.com) | the repository is deployed from it |
| A Claude subscription (Pro or Max) | [claude.ai](https://claude.ai) | Claude Code authenticates with it; see the note below |
| A Render account | [render.com](https://render.com) | free container hosting |
| Chrome on Android | [Play Store](https://play.google.com/store/apps/details?id=com.android.chrome) | Firefox has no speech recognition |

The branch being deployed:
[`claude/voice-shell-claude-code-77wwh2`](https://github.com/aisarus/Ccvoice-/tree/claude/voice-shell-claude-code-77wwh2)

> **About the subscription.** A Claude Pro or Max subscription is fine for an
> instance you run for yourself. Since February 2026 Anthropic no longer permits
> subscription OAuth credentials to be used from third-party products, so a
> Render service that other people talk to needs its own `ANTHROPIC_API_KEY`
> instead. A public URL plus a shared token is a service — keep the URL to
> yourself, or use an API key.

---

## Step 1. Create the service on Render (5 minutes plus 5–10 minutes of build)

1. Open [dashboard.render.com](https://dashboard.render.com) → **Get Started** →
   **Sign in with GitHub**, and allow access to the `aisarus/Ccvoice-`
   repository.
2. **New +** → **Blueprint** ([direct link](https://dashboard.render.com/blueprints)).
3. Pick the `aisarus/Ccvoice-` repository.
4. In the branch field put `claude/voice-shell-claude-code-77wwh2`, not `main`.
5. **Apply**. Render reads [`render.yaml`](../render.yaml) and builds
   [`Dockerfile`](../Dockerfile).
6. Nothing to fill in: the only required variable (`VOICE_TOKEN`) is generated
   for you, and Claude is connected in step 4.
7. Wait for the status **Live**. The first build is slow — Node and the Claude
   Code CLI are installed inside it.

<details>
<summary>If the Blueprint did not catch</summary>

**New +** → **Web Service** → the same repository and branch → Language/Runtime:
**Docker** → Instance Type: **Free** → **Create Web Service**. Then add the
`VOICE_TOKEN` variable by hand, with any long random value (Environment → Add
Environment Variable).
</details>

## Step 2. Take the access token

On the service page press **☰** (the three bars at the top left) → in the
service menu, **Environment**. Or scroll the service page down — the
`Environment Variables` section comes after `Settings`.

There is a list of variables there. You need the `VOICE_TOKEN` line; the value
is hidden — press the eye / **Show**, or the copy icon next to it.

**The easiest thing is to set it yourself:** press the `VOICE_TOKEN` value,
delete it, type your own long string (20+ characters, Latin letters and digits,
for example `sonyvoice7412kqmz`), **Save Changes**. The service restarts within
a minute, and you know your token without decoding anything.

What it is **not**:

- it is **not** the `Service ID` (`srv-...`) — that is only the service
  identifier;
- it is **not** the Claude subscription token — that appears in step 4.

It is the password to your voice access: whoever knows it can talk to your
Claude. Do not forward it. You can change it at any time in the same place — a
new value kills the old link instantly.

## Step 3. Open it on the phone

```
https://YOUR-SERVICE.onrender.com/?token=TOKEN_FROM_STEP_2
```

Render shows the service address at the top of the service page — a link like
`https://voice-shell.onrender.com`, right under the name and the **Live** badge.
Open it **in Chrome** and allow the microphone. The token is saved in the
browser and disappears from the address bar at once; after that the service
address alone is enough.

## Step 4. Connect Claude

At the top there will be a block saying "Claude не подключён".

1. **подключить подписку** — `claude setup-token` starts on the server.
2. **открыть авторизацию ↗** — log in to your Claude account, allow access, copy
   the code from the page.
3. Paste the code into the field → **готово**.
4. A long-lived token appears (a year) and a **копировать** button.
5. Paste it into Render → **Environment** → `CLAUDE_CODE_OAUTH_TOKEN` →
   **Save**. Without this step everything works, but after a container restart
   the subscription has to be connected again.

If you need separate per-token billing, or anyone other than you will use this
service, put in an `ANTHROPIC_API_KEY`
([console.anthropic.com](https://console.anthropic.com/settings/keys)) instead
of a subscription. The application understands both.

## Step 5 (optional). Give Claude a working repository

By default Claude Code works in an empty directory inside the container. To let
it see a real project and push to it:

| Variable | Value |
|---|---|
| `WORKSPACE_REPO` | `https://github.com/aisarus/Ccvoice-.git` or another repository |
| `GITHUB_TOKEN` | a token with `repo` rights — [github.com/settings/tokens](https://github.com/settings/tokens) → Generate new token (classic) |

---

## Test: seven checks in order

Press the button (on screen or on the headphones), say the phrase, press again
or fall silent. After the answer the microphone comes back on by itself — the
conversation can continue hands-free.

The spoken phrases below are Russian, because the shell's own phrases are
Russian. See the note at the top of [`VOICE.md`](VOICE.md).

| # | What to say | What should happen | What it checks |
|---|---|---|---|
| 1 | "Посчитай сколько будет семнадцать процентов от четырёх тысяч двухсот" | a spoken answer (714), a `chat` badge in the feed | microphone, recognition, routing to chat, speech synthesis |
| 2 | "Запиши идею про второе ухо" | "Записал.", a `note` badge | the inbox target and prefix routing |
| 3 | "Покажи какие файлы в репозитории" | a `code` badge, an answer listing files | the long-lived Claude Code session |
| 4 | "Да" (answering a question, if there was one) | a confirmation sound, "разрешил" in the feed, the work carries on | voice approvals |
| 5 | Press the **код** chip, say "ну такое себе" | goes to `code`, not to chat | forcing the target |
| 6 | Press **авто**. Have someone speak a couple of metres from the phone (or play a video with speech) and hold the button | "реплика не исполнена (role_gate)" in the feed, nothing runs | telling the master from a bystander |
| 7 | Speak quietly, almost in a whisper, holding the phone to your mouth | the utterance is accepted as yours | the whisper profile |

The second ear (optional): press **ambient: passive**, then **чужие реплики:
вкл**, have the other person say a number, then ask "какую цифру он назвал". The
answer comes out of the local buffer. Turning ambient off wipes the buffer.

---

## All the controls

| Control | Where | What it does |
|---|---|---|
| The dot and the state word | top | `IDLE` waiting · `LISTENING` listening · `THINKING` thinking · `SPEAKING` speaking · `WORKING` working |
| Token field | appears if access is not confirmed | paste `VOICE_TOKEN` by hand |
| Subscription block | appears if Claude is not connected | the whole of step 4 |
| **авто / код / чат / заметка** | a row of chips | forces the target; holds until you go back to **авто**; a spoken "в чат …" overrides the chip |
| **ambient** | chip | `off → passive → assist`; `passive` is a local ten-minute buffer |
| **чужие реплики** | chip, visible when ambient is on | explicit opt-in to writing other people's speech into the buffer; turning it off wipes what was collected |
| The big button | centre | one press starts an utterance, a second one ends it; a press during an answer cuts the speech off |
| **The headphone button** | on the headset itself | the same: press to talk, press to finish. It works through the media session, so press the big on-screen button once to activate it |
| **разговор** | chip | after an answer the microphone comes back on for 15 seconds — you can keep talking without pressing anything |
| **слушать всегда** | chip | the microphone stays on; only utterances starting with "Клод…" are executed. A stand-in for the wake word: audio goes to the browser's recognition service and the battery drains |
| The bar under the button | centre | microphone level: whether the phone can hear you |
| The feed | bottom | the badge shows who spoke and where the utterance went |
| The hint line | under the button | target availability and the authentication method |

## Sounds

| Sound | Meaning |
|---|---|
| short, rising | heard you, listening |
| soft click | utterance accepted |
| two short | your decision is needed |
| soft ding | done |
| falling | error |
| low / high | went to code / to chat |

## Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `VOICE_TOKEN` | yes (generated) | the password to voice access |
| `CLAUDE_CODE_OAUTH_TOKEN` | in practice yes | the Claude subscription; set in step 4 |
| `ANTHROPIC_API_KEY` | no | the alternative to a subscription |
| `WORKSPACE_REPO` | no | a repository cloned at startup |
| `GITHUB_TOKEN` | no | so Claude can push (`GH_TOKEN` is read too) |
| `AMBIENT_SUBMODE` | no | starting mode: `off` (default), `passive`, `assist` |
| `ROUTER_MODEL` | no | `auto` — on an uncertain utterance a model picks the target; `off` — dictionary only, no extra round trip to Claude |
| `PERMISSION_MODE` | no | `auto` (default) — nothing is asked; `guarded` — destructive things are; `ask` — everything but safe reads |
| `PROACTIVE` | no | `watch` (default) — mention a failed build; `fix` — mention it and fix it; `off` — stay quiet |
| `QUIET_HOURS` | no | `23-8` by default: the hours the watcher stays quiet. `off` — do not |
| `VOICE_MEMORY` | no | path to the memory file instead of `.voice-shell/memory.md` in the working directory |
| `VOICE_GLOSSARY` | no | your own names, comma separated (people, services, projects) — Claude will recognize them in a mangled utterance |
| `VOICE_ENV_FILE` | no | where this environment file itself lives; the daemon appends the subscription token to it |
| `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` | no | the Telegram bridge; `setup-telegram.sh` writes both |
| `TELEGRAM_API` | no | a different Bot API address, for a proxy or a test server |
| `CLAUDE_CONFIG_DIR` | no | where the CLI keeps its own configuration |
| `PORT`, `HOST`, `WORKSPACE_DIR`, `NOTE_PATH` | no | set by the platform, or defaulted |

---

## If something is wrong

| Symptom | Cause and what to do |
|---|---|
| "нужен токен доступа" | opened without `?token=` — paste `VOICE_TOKEN` into the field on the page |
| "нет доступа к микрофону" | the address is not `https://`, or permission was not granted in Chrome |
| there is a button but no text | Chrome is required: Firefox on Android has no speech recognition |
| "Claude недоступен: не подключена подписка" | do step 4, or set `CLAUDE_CODE_OAUTH_TOKEN` |
| it asks for the subscription again after a restart | the token was not saved in Environment (step 4, item 5) |
| "код не принят или токен не выдан" | the authorization code is short-lived — go through step 4 again |
| the first answer takes a minute | Render's free plan sleeps after 15 minutes of idling |
| the utterance went to the wrong target | say "не туда", or pick the target with a chip |
| your quiet utterance was not executed | a whisper from a cold start gives `unknown`; repeat louder, or say something inside an open dialogue window |
| "Claude недоступен: не установлен claude-agent-sdk" | not about the token: the library is missing in the container. Rebuild the service |
| nothing is asked before dangerous things | by design: `PERMISSION_MODE` is `auto` by default. Set `guarded` |

On your own server instead of Render there is `doctor.sh`, which checks all of
this with one command. Full diagnostics:
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

## What is not here yet — so you do not go looking

- **A local wake word.** The "слушать всегда" mode catches "Клод", but it is the
  browser recognizing it, not the device: audio goes out and the battery drains.
  A real local wake word exists in the Android app, not in the browser client.
- **Voice "стоп".** While the microphone is not listening continuously there is
  nothing to catch "стоп" with: interrupting is by button, including the one on
  the headphones.
- **Recognition and synthesis are the browser's.** In Chrome that means the
  audio of your utterance goes to Google's recognition service. Recognition on
  the daemon side is stage S1 and does not exist.
- **Ambient assist.** The buffer and answers from it work; proactive
  trigger-driven suggestions do not.
- **Headphone microphone.** There is no separate device choice — the browser
  takes whatever the system considers the default.

---

## Where else this can be deployed

Render was picked on one criterion: the whole path can be walked from a phone,
with no CLI. There is nothing Render-specific in the code. What a host has to
provide:

| Needed | Why |
|---|---|
| a long-lived process (a container, not serverless) | one Claude Code session is held between utterances |
| outbound WebSocket | voice, state and approvals travel over it |
| HTTPS | otherwise the browser gives no microphone |
| one open port | the client and the WebSocket are deliberately on the same one |
| a writable filesystem | the working copy of the repository, and the subscription token |
| deploy from a browser | you have no PC, and CLI deploys need a terminal |

**Vercel, Netlify and Cloudflare Workers do not work** — there a process lives
only for the duration of a request: a WebSocket server cannot be raised, and the
session would die between utterances. This is not a question of plan.

**These do:** Render, Railway, Koyeb (all three deploy from a browser), Fly.io
and any VPS (needs a terminal). For a VPS there is
[`docker-compose.yml`](../docker-compose.yml): `VOICE_TOKEN=... docker compose
up -d` and any reverse proxy with TLS.
