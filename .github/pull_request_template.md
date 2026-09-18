## What broke, or what was missing

<!-- The real situation this came out of, in one or two sentences.
     "Said 'roll that back' twice and the second one did nothing" beats
     "improve checkpoint handling". -->

## What this changes

<!-- One or two sentences. The diff says the rest. -->

## Checks

- [ ] `python3 -m pytest tests -q`
- [ ] `python3 scripts/validate_spec.py`
- [ ] Touched `spec/voice-shell.json`? The validator agrees, and the code follows the spec rather than the other way round.
- [ ] New behaviour has a test named after the behaviour, and its docstring says what went wrong in real life.
- [ ] No secrets, tokens, keys, personal addresses or hostnames in the diff.
