# When it does not work

One command first. It answers most of it.

```bash
sudo bash /opt/voice-shell/scripts/doctor.sh --fast
```

At the bottom of the report is a section called `итог` ("summary") listing what
was found. Empty means the problem is not on the server — go to
[Phone](#the-phone).

`doctor.sh` prints its report in Russian; the section names are quoted below as
they appear on screen. Exit code `0` means it found nothing wrong, `1` means it
did.

Without `--fast` two slow checks are added: a direct question to the CLI (up to
90 s) and a full utterance through the daemon (up to 2 minutes). Run those when
the fast report is clean and it still does not work in real life.

Russian original: [`ЕСЛИ-НЕ-РАБОТАЕТ.md`](ЕСЛИ-НЕ-РАБОТАЕТ.md).

---

## Symptom → what to check → how to fix it

### It does not hear anything at all

| What to check | How to fix it |
|---|---|
| The app is in the running list, with the service line in the notification shade | open the app; without notification permission Android does not allow a foreground service at all |
| What the notification says about the microphone | `микрофон: телефон` while a headset is on — press **микрофон: гарнитура** |
| Whether the wake word model (45 MB) downloaded | the first launch fetches it; with no network it never arrives, and the wake word is not recognized at all |
| Whether the daemon answers at all | `doctor.sh --fast`, section `что думает сам демон` ("what the daemon itself thinks") |

### It hears you every other time

Nearly always one thing: said in one breath instead of two beats.

> "Клод" · pause · wait for the tone · phrase

The wake word is heard by a small local model, the utterance by the phone's
recognizer, and that one does not start instantly. Finish before the tone and
after 3.5 seconds what the small model heard is sent instead — worse, but not
silence. That is where "he misunderstood me" comes from.

| What to check | How to fix it |
|---|---|
| Whether you wait for the tone after "Клод" | wait. The tone is permission to speak |
| Whether the headset is the one listening | the line in the notification: `микрофон: гарнитура (bluetooth)` |
| Whether it is an echo | our own synthesis in the microphone is discarded, but that works worse through a speaker than through headphones |
| Words that sound like "Клод" | "клот", "клад", "плод" count as the wake word too. "Код" and "чат" do not — those are how commands start |

### It does not understand a command

Command phrases from every language the shell knows are matched at the same
time, so there is nothing to switch before speaking: «откати последнее» and
"undo the last thing" are live in the same utterance slot. If a command is
ignored anyway, it is one of these.

| What to check | How to fix it |
|---|---|
| Whether the phrase is in the table at all | [`VOICE.md`](VOICE.md) lists every one in Russian and English; Spanish and Chinese are in [`lexicon.py`](../daemon/voice_claude/lexicon.py). A near miss is not a match — the tables are exact phrases, not keywords |
| Whether the command opened the utterance | undo, memory and the target prefixes have to come first. Filler in front is stripped ("claude", «слушай», "ok", «пожалуйста»); a clause is not. «А это можно откатить?» is a question and stays one |
| Whether it was Hebrew | there are no phrase tables for Hebrew — it is a recognition language only. Spoken Hebrew reaches Claude as ordinary speech, and the shell's own commands do not fire |
| What the phone actually heard | the notification shows `heard on the phone: …` and what was sent. If the words there are wrong, the problem is recognition, not the command table |
| Whether the daemon sees the same words | `say.py` sends text straight through, skipping recognition — see [Checking by hand](#checking-by-hand-without-a-microphone) |

### It answers in a language you did not expect

Usually that is the shell answering in the language it heard, which is what it is
supposed to do, not a fault. The answer's language is decided per utterance, in
this order: a language pinned by voice, then the language of the utterance
itself, then what the phone declared when it connected, then `VOICE_LANG`, then
English. One sentence in another language — or one the detector reads as another
language — moves the next answer with it.

| What to check | How to fix it |
|---|---|
| Whether a language is pinned | "Claude, English" pins the answers and beats everything else, including the language you are speaking. "Claude, as I asked" unpins it. A pin set days ago is the usual reason a whole session comes back in the wrong language |
| Whether the utterance really was in that language | Latin script is decided by a short list of common words, so a short English-looking phrase inside a Spanish sentence can tip it. The next utterance re-decides on its own |
| What the phone declared | the app's language switch, or the chip in the browser client, is sent in `hello` and is used when the utterance itself settles nothing |
| `VOICE_LANG` | the deployment fallback, used when none of the above says anything |

To move the fallback:

```bash
echo 'VOICE_LANG=en' | sudo tee -a /etc/voice-shell.env
sudo systemctl restart voice-shell
```

It takes `en`, `ru`, `es` or `zh`. It is the last resort and not an override: a
pinned language, an utterance whose language the shell recognises, and the
language the phone declared all win over it. To hold the answers to one language
whatever you speak, pin it by voice.

Listening is not affected by any of this, pin or no pin. The shell goes on
matching commands in every language whatever it is answering in.

### It says nothing back

| What to check | How to fix it |
|---|---|
| Did "Работаю" arrive? | then the utterance got through and Claude is working. Long work is not silent: first "Работаю", then repeats |
| `doctor.sh --fast` → `служба` ("service") | not `active` — `journalctl -u voice-shell -n 50` |
| `doctor.sh --fast` → `что думает сам демон` | no answer — the daemon is down; `systemctl restart voice-shell` |
| Answer is `ok` instead of JSON | the `VOICE_TOKEN` in the environment file is not the one the daemon is running with. `systemctl restart voice-shell` |
| Sound goes to the speaker, not the headphones | while the headset channel is up, synthesis goes into the call stream. Check that the channel is up |

### "code: stub" — it answers with a placeholder

You hear "Claude недоступен: …". That is an honest answer, not a broken
connection: the loop ran end to end and there was nothing to answer with. The
reason is always named in the sentence itself. There are three:

| What the daemon said | What it means | How to fix it |
|---|---|---|
| "не установлен claude-agent-sdk" | the library is missing, the token is irrelevant | `/opt/voice-shell/.venv/bin/pip install -r /opt/voice-shell/daemon/requirements.txt` |
| "токен доступа неверный — …" | there is a token, and it is no good | paste it again, see below |
| "не подключена подписка Claude" | no token, no key, and the CLI is not logged in | `claude setup-token` on the server |

Trap: `/healthz` does not tell these three apart. It will show
`"credential": "cli"`, `"credential_problem": null` and `"code": false` at the
same time — when the package is what is missing. That is why `doctor.sh` checks
the package separately, in the section `библиотека Claude` ("Claude library").

Reconnect the subscription:

```bash
claude setup-token
sudo nano /etc/voice-shell.env      # line CLAUDE_CODE_OAUTH_TOKEN=sk-ant-...
sudo systemctl restart voice-shell
```

The token has to be one line, no quotes, no line breaks. `doctor.sh` complains
"ПОРТИТСЯ — есть не-ASCII символы" if pasting through a phone put stray
characters in it.

Which credential you are allowed to use is a separate question — see
[Which credential](../README.md#which-credential). A Pro or Max subscription
covers you running this for yourself. If anyone else will use the instance, it
needs its own `ANTHROPIC_API_KEY`.

### The update will not install

`git pull` on the working copy stops at "divergent branches" and waits for you
to choose a merge strategy. So updating is its own command:

```bash
sudo bash /opt/voice-shell/scripts/update-server.sh
```

It does fetch + reset: the version in the branch is exactly what is wanted,
there is nothing to merge. Before resetting it prints what is about to
disappear and sets a `before-update-…` tag.

| What it says | What to do |
|---|---|
| "нет рабочей копии в /opt/voice-shell" | install again: `install-server.sh` |
| "нужен root" | use `sudo` |
| "не смог забрать ветку … с origin" | no network, or no access to GitHub |
| "спека и код разошлись" | the service was NOT restarted, the old version is still running. Roll back using the tag on the last line |
| "служба не поднялась" | 20 lines of log are printed directly underneath |

Get back what was there before the update:

```bash
git -C /opt/voice-shell reset --hard before-update-YYYYMMDD-HHMMSS
sudo systemctl restart voice-shell
```

List the tags: `git -C /opt/voice-shell tag -l 'before-update-*'`. The last ten
are kept.

### The APK will not install

> **Before anything else: the released APK is signed with a debug key that is
> committed to this repository together with its password.** The keystore is
> `android/app/voice-shell.keystore`; the password is in plain text in
> `android/app/build.gradle.kts`. It is deliberate — otherwise every CI build
> would get a random key and no update would install over the previous one. The
> consequence is that anyone can build an APK signed with that same key and
> Android will take it as an update to yours. **Install the APK only from this
> repository's releases.** For your own builds, make your own keystore and keep
> it out of the repository.

| Symptom | Cause | How to fix it |
|---|---|---|
| "App not installed" over an existing one | the old build was signed with a different key | uninstall the app and install again. All builds with the one key (`app/voice-shell.keystore`) install over each other |
| Android will not let you install | installs from unknown sources | allow it for the browser you are downloading with |
| A zero-byte file downloaded | the build did not finish uploading the APK | take `voice-shell-apk.zip` from the same release |

### The APK link does not work

The link points at a release with a floating tag, `apk-latest`, which CI
rewrites on every push to the working branch:

<https://github.com/aisarus/Ccvoice-/releases/download/apk-latest/app-debug.apk>

| What to check | How to fix it |
|---|---|
| Whether the file is in the release | open the [release page](https://github.com/aisarus/Ccvoice-/releases/tag/apk-latest) — it shows what was uploaded |
| Whether the build passed | the Actions tab, workflow `android` |
| 404 instead of a file | the build is still running, or the upload step failed. The build ends with a step of its own, "Проверить, что APK лежит в релизе", which goes red if the link is broken |

The APK used to be uploaded by a ready-made action that hung for sixteen minutes
and left the release without a file. The upload is direct now, with three
attempts and a visible error.

### It does not ask before anything dangerous

That is the design: `PERMISSION_MODE` defaults to `auto` and nothing is asked.
Voice approvals are turned on explicitly:

```bash
echo 'PERMISSION_MODE=guarded' | sudo tee -a /etc/voice-shell.env
sudo systemctl restart voice-shell
```

| Mode | What it asks about |
|---|---|
| `auto` (default) | nothing |
| `guarded` | destructive things only (`rm -rf`, `push --force`, `reset --hard`, secrets) |
| `ask` | everything except reads that are known to be safe |

### It starts talking at the wrong moment

A watcher looks at failed GitHub builds and speaks on its own.

| Variable | Meaning |
|---|---|
| `PROACTIVE=off` | stay quiet |
| `PROACTIVE=watch` (default) | mention a failed build |
| `PROACTIVE=fix` | mention it and try to fix it |
| `QUIET_HOURS=23-8` (default) | stay quiet at night; `off` — do not |

No more than twice in five minutes, and never twice about the same thing.

---

## The phone

The server report is clean and it still does not work in real life — then it is
the phone.

| Symptom | How to fix it |
|---|---|
| The app is silent after a reboot | in the app, allow autostart and lift the battery usage restriction |
| The service dies after a few minutes | the same: the system kills a foreground service when battery saving is on |
| Connects and drops immediately | check the address and token in the settings; an address without TLS needs `http://`, not `https://` |
| It mishears whole sentences in one language but not another | the app asks Android to recognise every language it knows, starting with the one picked on its screen, but only a recognition service that honours the request actually does it. Move the picked language by voice — "Claude, listen in English" — or on the app's screen; if multilingual recognition is what spoiled the main language, "listen only" stops it asking. "Claude, English" moves neither: that pins the answers. The daemon's command tables need no switching, they are all matched at once |
| The voice in the headphones is quiet or "telephone-like" | that is correct: the headset microphone channel is narrowband. The **микрофон: телефон** button gives the wide band back and loses the headset microphone |
| Stress marks sound wrong, or you can hear a "plus" | the **ударения** button: "знаком" instead of "авто", check with the "скажи" field |

Details about the headset microphone and stress marks are in
[`android/README.md`](../android/README.md) (Russian).

---

## Checking by hand, without a microphone

An utterance in text form goes the whole way except recognition: connection,
token, routing, Claude, answer formatting.

```bash
sudo /opt/voice-shell/.venv/bin/python /opt/voice-shell/scripts/say.py "скажи привет"
```

Exit code `0` — the answer is real, `1` — a stub or an error. The target can be
named: `--target code`.

Ask the daemon directly:

```bash
curl -s "http://127.0.0.1:8787/healthz?token=$(sudo sed -n 's/^VOICE_TOKEN=//p' /etc/voice-shell.env)"
```

| Field | What it means |
|---|---|
| `state` | `IDLE` waiting · `THINKING` thinking · `WORKING` working · `SPEAKING` speaking |
| `credential` | `subscription` · `api_key` · `cli` · `none` |
| `credential_problem` | a human-readable reason for refusal, or `null` |
| `code`, `chat` | `false` — that target answers with a stub |
| `permission_mode` | `auto` · `guarded` · `ask` |
| `workspace` | the directory Claude Code works in |
| `github` | whether Claude has access to GitHub |

Without a token `/healthz` answers `ok` and nothing more — enough for the platform's
liveness check, and it gives no state away to a passing stranger.

---

## Where to look next

| Where | What is there |
|---|---|
| `journalctl -u voice-shell -f` | the daemon's live log |
| `systemctl status voice-shell` | why the service did not come up |
| [`VOICE.md`](VOICE.md) | which phrases the system understands |
| [`DEPLOY.md`](DEPLOY.md) | deploying to Render, and the browser client |
