# Contributing

## Getting it running

You need Python 3.11 or newer. Nothing else is required to work on the daemon
— no phone, no Claude subscription, no server.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r daemon/requirements-dev.txt
```

```bash
cd daemon
python -m voice_claude --workspace /tmp/workspace
```

It prints the port and a generated access token. Open
`http://localhost:8787/?token=<that token>` and the web client is the same
client the phone is; a browser microphone works on `localhost` without HTTPS.

With no Claude credentials at all — no `CLAUDE_CODE_OAUTH_TOKEN`, no
`ANTHROPIC_API_KEY` and no logged-in Claude Code CLI — the `code` and `chat`
targets run as stubs: the whole voice loop still works end to end and you hear
"Claude unavailable" with the reason at the far end. That is deliberate; it
lets you debug the voice path without credentials anywhere near it. The startup
banner tells you which way it went (`code : ready` or `code : stub`).

The Android app is built with Gradle 8.9 and JDK 17 (`cd android && gradle
testDebugUnitTest`). CI builds it on every push to `android/`, so you do not
need a local Android SDK to change Kotlin and have it checked.

## Before you open a pull request

```bash
python3 -m pytest tests -q
python3 scripts/validate_spec.py
```

Both must pass. CI runs exactly these two on Python 3.11 and 3.12, plus
`shellcheck --severity=warning` over every shell script.

## The spec is the source of truth

`spec/voice-shell.json` describes how the system is supposed to behave —
speaker classification, thresholds, routing, permissions, the lot. The code
follows it, not the other way round.

`scripts/validate_spec.py` checks it against `spec/voice-shell.schema.json` and
then runs consistency checks the schema cannot express: that weight profiles
reference signals that are actually declared, that thresholds are ordered
sensibly, that cross-references between sections resolve. If you change
behaviour, change the spec in the same pull request and let the validator agree
with you.

If the code cannot do what the spec says — which happens; the phone measures
two of six acoustic features and the spec asks for six — say so in a comment
where the gap is, rather than quietly editing the spec down to what the code
manages.

## Two languages, on purpose

**Comments and commit messages are in Russian.** This is not an oversight and
please do not "fix" it. The project is maintained in Russian, the voice
commands are in Russian, and a comment explaining why a threshold is 1.5 is
worth more to the person maintaining this than a translated one.

**Documentation is in English**: `README.md`, this file, `SECURITY.md`, issue
templates, and docstrings of anything a reader outside the project will meet
first. The files under `docs/` are Russian, because they are operational
instructions for the person running their own server.

Identifiers in Python may be Russian where that reads better — you will find
`остаток`, `плохие`, `ДОКЛАДЧИКИ` in the codebase. In shell scripts they may
not: bash silently treats `имя=значение` as a command and fails halfway
through, after the user has already pasted their token. There is a test that
enforces this.

## How tests are written here

Two rules, both visible throughout `tests/`.

**Name the test after the behaviour, not the function.** Not
`test_parse_command`, but `test_the_script_stops_on_the_first_failure`. Reading
the list of test names should read as a list of promises the system makes.

**The docstring says what went wrong in real life.** Every test here exists
because something broke for an actual person, and the docstring records what
that was:

```python
def test_variable_names_are_latin(script):
    """«имя=Ccichekbot: command not found» — настоящая поломка у человека."""
```

```python
def test_the_script_stops_on_the_first_failure(script):
    """Без set -e установщик доходит до конца, оставив половину сделанной."""
```

When someone later wonders whether a check is still worth its runtime, that
sentence is the answer. A test whose docstring only restates its name is a test
nobody will dare delete and nobody will understand.

If you are fixing a bug, write the test that fails first, and put the bug in
its docstring.

## Commits and pull requests

Commit messages describe the change from the outside, in Russian, in one line:
`Реплику человека слышно, а причину поломки — понятно`. The history reads as a
log of what changed for the user, not of which files were touched.

The pull request template asks what broke and what this changes. Two sentences
each is enough; the diff says the rest.

## Security

Do not open a public issue for a vulnerability. `SECURITY.md` says where to
send it, and describes the parts of the threat model that are already known and
accepted, so you can tell a finding from a documented trade-off.
