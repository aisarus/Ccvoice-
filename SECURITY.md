# Security

Voice Shell puts a microphone in a room and a shell on a machine, and connects
the two. That is the whole point of it, and it is also the whole security
problem. This page describes what the system actually does today, not what it
is meant to do eventually.

Nothing here is a warning label. It is a list of facts you need in order to
decide where to run this and who is allowed near it.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: the **Security** tab of this
repository → **Report a vulnerability**. It opens a private thread with the
maintainer; nothing is public until it is fixed.

If that form is not available to you, open a normal issue that says only
"security, need a private channel" and no details, and wait to be contacted.
Do not put the details in a public issue.

Expect a first reply within a week. There is no bounty, no SLA and no embargo
policy beyond common sense: this is one person's project.

## The threat model, plainly

### The microphone is always on

The Android app runs a foreground service with
`android:foregroundServiceType="microphone"` and holds `RECORD_AUDIO` for as
long as it is running. It starts on boot (`RECEIVE_BOOT_COMPLETED`). There is
no push-to-talk. Everything said in the room passes through the recogniser.

### Where the speech goes

Two different recognisers, two different destinations:

1. **The wake word** is recognised on the phone, by a local Vosk model
   (`WakeWordEngine.kt`). Audio for this never leaves the device.
2. **The command after the wake word** goes to Android's
   `SpeechRecognizer` (`VoiceService.kt`). On most phones that is Google's
   speech service, so the audio of the command leaves the device and goes to
   Google — the same as any other dictation on that phone. The resulting
   *text* is then sent over a WebSocket to the server you deployed.

So: the room is listened to locally; what you say to Claude goes to Google as
audio and to your own server as text. Your server then hands it to Claude Code,
which sends it to Anthropic. If any of those three hops is unacceptable for
what you work on, this is not the tool for that work.

### "Master" and "bystander" is not identity

`daemon/voice_claude/speaker.py` classifies each utterance as `master`,
`bystander`, `unknown` or `self_echo`. The spec describes six acoustic
features. The Android app measures **two** of them — relative loudness and
signal-to-noise ratio (`Acoustics.kt`) — because the recogniser only exposes a
stream of RMS values. The voiceprint feature is not implemented on either side;
the daemon zeroes the weight of anything it was not sent.

The practical consequence, stated without softening: **loudness near the
microphone is what makes you the owner.** A guest who leans toward the phone
and speaks up is classified as the owner and is obeyed. A neighbour shouting
through a thin wall can be, too. A television can be. Low confidence produces
`unknown` rather than a guess, which helps against quiet background chatter and
does nothing against a loud stranger standing next to the phone.

Treat physical access to the room as full access to the machine.

### The default permission mode asks nothing

`PERMISSION_MODE` defaults to `auto` (`daemon/voice_claude/policy.py`). In
`auto`, every tool call Claude Code wants to make is allowed without asking.
Editing files, running commands, deleting things — none of it produces a
question.

There is a curated list of pre-approved read-only tools and a `NEVER_PREAPPROVE`
list of destructive patterns (`rm -rf`, `git push --force`, `git reset --hard`,
`curl | sh`, anything matching `secret`/`token`/`password`/`.env`, and others).
**In `auto` mode none of that list is consulted** — `decide_in_mode` returns
`allow` before reaching it. The lists take effect only in the other two modes:

| `PERMISSION_MODE` | behaviour |
| --- | --- |
| `auto` (default) | nothing is ever asked |
| `guarded` | destructive actions are asked out loud, everything else runs |
| `ask` | read-only actions run, everything else is asked out loud |

`auto` also decides what Claude Code itself is told. In `auto` the session is
started with Claude Code's `permission_mode = "bypassPermissions"`
(`daemon/voice_claude/targets.py`), so Claude Code's own prompts are off as
well — not merely answered automatically by the daemon. In the other modes the
session runs with `acceptEdits` plus a callback that asks out loud.

If the machine matters, set `PERMISSION_MODE=guarded` or `ask` in
`/etc/voice-shell.env`. The default is `auto` because the project was built for
one person talking to their own machine, and asking about every `ls` makes it
unusable. That is a convenience trade, and it is the single biggest one here.

### The daemon runs as root

The systemd unit written by `scripts/install-server.sh` has no `User=`, so it
runs as root. `NoNewPrivileges=yes` is set, and that is the extent of the
confinement. The code knows this and works around it: Claude Code refuses to
start as root with permissions disabled, so under root the daemon keeps
`acceptEdits` and grants everything through its own callback instead
(`running_as_root()` in `daemon/voice_claude/targets.py`). The effect of `auto`
is the same either way.

So on a default install, a voice accepted as the owner acts as root on that
machine. If that is more than you want, add a `User=` to
`/etc/systemd/system/voice-shell.service`, give that user the workspace, and
restart. Nothing in the daemon requires root.

### The way out is deliberately narrow

The Telegram bridge (`daemon/voice_claude/telegram.py`) is the only path by
which a file leaves the machine on a voice command. It:

- sends to exactly one chat, `TELEGRAM_CHAT_ID`, fixed in the environment
  file — there is no way to say "send it to someone else";
- refuses anything outside the workspace directory;
- refuses paths that look like secrets: `.env`, `*.pem`, `*.key`, `id_rsa`,
  `credentials*`, `*.p12`, `*.keystore`, `*.jks`, and anything containing
  `secret`, `token` or `password`;
- refuses files over 45 MB.

The refusals are name-based, not content-based. A secret in a file called
`notes.txt` will be sent.

### Transport is whatever you configured

`network_security_config.xml` permits cleartext, because the daemon is expected
to live on a home network or a tailnet without a certificate. If you point the
app at an `http://` address, the access token and every transcript cross the
network in the clear. Point it at `https://`/`wss://` and it does not; the app
switches scheme automatically.

The web client accepts the access token as a URL query parameter
(`/?token=…`) and then keeps it in `localStorage`. That URL lands in browser
history, and in the logs of anything that sits in front of the server.

### Tokens

Secrets live in `/etc/voice-shell.env`, owned by root, mode `600`, written by
the setup scripts with `umask 077`. They are not in this repository and never
have been:

- `CLAUDE_CODE_OAUTH_TOKEN` — Claude Code credentials
- `VOICE_TOKEN` — the pre-shared token between client and daemon. If unset, the
  daemon generates a random one at startup, which means it changes on every
  restart and no client can connect until you read the new one.
- `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`

`scripts/doctor.sh` reports whether each one is set, its length, and its first
nine characters. For the Claude tokens those nine are the fixed `sk-ant-oa`
prefix. `VOICE_TOKEN` is `secrets.token_urlsafe(24)` and random throughout, so
for it those nine characters are nine characters of the real secret. Redact
that one line before pasting a doctor report anywhere public; the rest of the
report carries no secret values.

## APK signing key

This is the one place where going public changed the facts, so it is described
in full.

`android/app/voice-shell.keystore` is committed to this repository, and its
password is in plain text in `android/app/build.gradle.kts`. While the
repository was private this was a convenience: CI signed every build with one
key, so each new APK installed over the previous one instead of forcing an
uninstall.

In a public repository, that key is public. Its fingerprint is:

```
SHA-256 22:51:33:35:DD:AD:D4:5C:07:D2:49:17:3C:3B:AD:52:37:60:3B:E0:4E:02:36:0C:7B:32:95:18:48:46:3F:73
```

Anyone can build an APK signed with it. Android accepts an update when the
signature matches, so such an APK installs **over** an existing Voice Shell
install, inheriting its data and its microphone permission. Treat any build
signed with this key as a debug build from an untrusted source, including the
ones this repository's CI currently produces.

The key cannot be rotated retroactively — it is in the git history and stays
there. What you can do is stop using it:

1. Run `scripts/release-key.sh`. It creates a keystore only you hold, prints
   its fingerprint, and prints the `base64 -w0` line to paste into GitHub.
2. Add two repository secrets: `ANDROID_KEYSTORE_BASE64` and
   `ANDROID_KEYSTORE_PASSWORD`.
3. Make the Gradle change below, if it is not already in place.

After that, the first install requires uninstalling the old app once, because
Android will not install over an APK signed with a different key. Subsequent
updates work as before.

### Current state of the wiring

The CI workflow (`.github/workflows/android.yml`) is already done: when
`ANDROID_KEYSTORE_BASE64` is present it decodes the keystore, checks the
password and the `voiceshell` alias before the build, passes the path as
`-Pkeystore=` and the password as `ANDROID_KEYSTORE_PASSWORD`, and afterwards
compares the APK's actual signer fingerprint against the key it meant to use.
A mismatch fails the build rather than shipping a build signed with the wrong
key. When the secret is absent, the build falls back to the repository key and
says so in the log and in `checksums.txt`.

`android/app/build.gradle.kts` does **not** yet read either of those. Until it
does, setting the secrets will make CI fail loudly with an explanation instead
of silently producing a wrongly-signed APK. The change needed is:

```kotlin
// Ключ подписи. В CI его подсовывают снаружи — из секретов GitHub; локально и
// в сборке без секретов остаётся отладочный ключ из репозитория. Он открытый:
// тем же ключом APK подпишет кто угодно, поэтому релизом такая сборка не бывает.
val externalKeystore = (project.findProperty("keystore") as String?)
    ?: System.getenv("ANDROID_KEYSTORE_FILE")
val externalPassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")

signingConfigs {
    create("shared") {
        if (externalKeystore != null && externalPassword != null) {
            storeFile = file(externalKeystore)
            storePassword = externalPassword
            keyAlias = "voiceshell"
            keyPassword = externalPassword
        } else {
            storeFile = file("voice-shell.keystore")
            storePassword = "voiceshell"
            keyAlias = "voiceshell"
            keyPassword = "voiceshell"
        }
    }
}
```

`scripts/release-key.sh` creates the key under the alias `voiceshell` with the
key password equal to the store password, so nothing else has to change.

## Verifying an APK you downloaded

```
apksigner verify --print-certs app-debug.apk
```

Compare the printed SHA-256 against the fingerprint you expect. The release
assets also carry a `checksums.txt` that records `sha256sum` of the files and
which key signed them.

## Out of scope

- Claude Code's own sandboxing and permission model. This project configures
  it; it does not implement it.
- The security of whatever Claude Code is pointed at. If the workspace is a
  repository with deploy credentials in it, voice now reaches those credentials.
- Anything downstream of Google's speech recogniser or Anthropic's API.
