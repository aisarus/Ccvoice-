#!/usr/bin/env python3
"""Validate spec/voice-shell.v0.1.json.

Runs JSON Schema validation when `jsonschema` is installed, and always runs
stdlib-only consistency checks (cross-references between sections, threshold
ordering, weight keys matching declared signals).

Usage: python3 scripts/validate_spec.py [spec.json] [schema.json]
Exit code 0 = valid.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "spec" / "voice-shell.v0.1.json"
SCHEMA = ROOT / "spec" / "voice-shell.schema.json"


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def schema_check(spec, schema, errors):
    try:
        import jsonschema  # type: ignore
    except ImportError:
        print("note: jsonschema not installed, skipping schema validation "
              "(pip install jsonschema)")
        return
    validator = jsonschema.Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(spec), key=lambda e: list(e.path)):
        errors.append("schema: %s at %s" % (err.message, "/".join(map(str, err.path))))


def consistency_check(spec, errors):
    si = spec["speaker_identification"]

    # 1. Weight profiles reference declared signals, and vice versa.
    signal_ids = {s["id"] for s in si["signals"]}
    for profile in si["scoring"]["weight_profiles"]:
        unknown = set(profile["weights"]) - signal_ids
        missing = signal_ids - set(profile["weights"])
        if unknown:
            errors.append("weights profile %r references unknown signals: %s"
                          % (profile["id"], sorted(unknown)))
        if missing:
            errors.append("weights profile %r is missing signals: %s"
                          % (profile["id"], sorted(missing)))

    # 2. Exactly one default weight profile.
    defaults = [p["id"] for p in si["scoring"]["weight_profiles"] if p.get("default")]
    if len(defaults) != 1:
        errors.append("expected exactly one default weight profile, got %s" % defaults)

    # 3. Threshold ordering.
    th = si["decision"]["thresholds"]
    if not 0 < th["bystander_max_p"] < th["master_min_p"] < 1:
        errors.append("thresholds must satisfy 0 < bystander_max_p < master_min_p < 1, got %s" % th)
    hy = si["decision"]["hysteresis"]
    if not hy["continuation_master_min_p"] < th["master_min_p"] < hy["after_bystander_master_min_p"]:
        errors.append("hysteresis thresholds must bracket master_min_p")

    # 4. Roles used in policy/templates exist.
    role_ids = {r["id"] for r in si["roles"]}
    for key in ("roles_that_execute", "roles_that_can_wake",
                "roles_that_can_barge_in", "roles_that_can_answer_permission"):
        unknown = set(si["policy"][key]) - role_ids
        if unknown:
            errors.append("policy.%s references unknown roles: %s" % (key, sorted(unknown)))
    unknown = set(si["claude_injection"]["templates"]) - role_ids
    if unknown:
        errors.append("claude_injection templates for unknown roles: %s" % sorted(unknown))
    if "self_echo" in si["claude_injection"]["templates"]:
        errors.append("self_echo must never be sent to Claude, but has an injection template")

    # 5. Only master may execute, wake, barge in, or answer permissions.
    for key in ("roles_that_execute", "roles_that_can_wake",
                "roles_that_can_barge_in", "roles_that_can_answer_permission"):
        if si["policy"][key] != ["master"]:
            errors.append("policy.%s must be exactly ['master'], got %s"
                          % (key, si["policy"][key]))

    # 6. Config defaults agree with the decision thresholds.
    cfg = spec["config_defaults"]["speaker_id"]
    if cfg["master_min_p"] != th["master_min_p"]:
        errors.append("config_defaults.speaker_id.master_min_p disagrees with "
                      "speaker_identification.decision.thresholds.master_min_p")
    if cfg["bystander_max_p"] != th["bystander_max_p"]:
        errors.append("config_defaults.speaker_id.bystander_max_p disagrees with "
                      "speaker_identification.decision.thresholds.bystander_max_p")
    if cfg["weight_profile"] not in {p["id"] for p in si["scoring"]["weight_profiles"]}:
        errors.append("config_defaults.speaker_id.weight_profile is not a declared profile")
    if cfg["permission_min_confidence"] != spec["permissions"]["safety"]["min_confidence"]:
        errors.append("permission_min_confidence disagrees between config_defaults and permissions.safety")

    # 7. State machine: transitions and definitions use declared states.
    states = set(spec["states"]["enum"])
    defined = {d["id"] for d in spec["states"]["definitions"]}
    if defined != states:
        errors.append("states.definitions %s != states.enum %s" % (sorted(defined), sorted(states)))
    for tr in spec["states"]["transitions"]:
        for side in ("from", "to"):
            value = tr[side]
            if value != "any" and value not in states:
                errors.append("transition %s->%s uses unknown state %r"
                              % (tr["from"], tr["to"], value))

    # 8. Earcons referenced elsewhere exist.
    earcons = {e["id"] for e in spec["states"]["earcons"]}
    for ev in spec["background_work"]["events"]:
        if ev["earcon"] not in earcons:
            errors.append("background_work event %r uses unknown earcon %r" % (ev["id"], ev["earcon"]))
    approval_earcon = spec["permissions"]["spoken_form"].get("earcon_before")
    if approval_earcon and approval_earcon not in earcons:
        errors.append("permissions.spoken_form.earcon_before uses unknown earcon %r" % approval_earcon)

    # 9. Activation shares add up and the button is not the main interface.
    methods = spec["activation"]["methods"]
    shares = {m["id"]: m.get("target_share_of_use") for m in methods if m.get("target_share_of_use")}
    total = sum(shares.values())
    if abs(total - 1.0) > 1e-6:
        errors.append("activation target_share_of_use sums to %s, expected 1.0" % total)
    if shares.get("headset_button", 1) > 0.05:
        errors.append("headset_button share must stay marginal (<= 0.05), got %s"
                      % shares.get("headset_button"))

    # 10. Latency targets are positive and barge-in agrees with interruptions.
    targets = {t["id"]: t for t in spec["latency_targets"]["targets"]}
    for tid, t in targets.items():
        if "max" in t and t["max"] <= 0:
            errors.append("latency target %r has non-positive max" % tid)
    if targets["barge_in_cutoff"]["max"] != spec["interruptions"]["barge_in"]["cutoff_ms"]:
        errors.append("barge_in cutoff disagrees between latency_targets and interruptions")

    # 11. Protocol message ids are unique and cover the speaker-role path.
    ids = [m["id"] for m in spec["protocol"]["messages"]]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        errors.append("duplicate protocol message ids: %s" % sorted(dupes))
    seg = next((m for m in spec["protocol"]["messages"] if m["id"] == "speech_segment"), None)
    if not seg or "role" not in seg["fields"] or "confidence" not in seg["fields"]:
        errors.append("protocol.speech_segment must carry role and confidence")

    # 12. AGC off / AEC on — the loudness features depend on it.
    if spec["audio"]["capture"]["agc"]["enabled"]:
        errors.append("audio.capture.agc must be disabled: AGC destroys absolute levels")
    if not spec["audio"]["capture"]["aec"]["enabled"]:
        errors.append("audio.capture.aec must be enabled: TTS echo would be read as speech")


def main(argv):
    spec_path = Path(argv[1]) if len(argv) > 1 else SPEC
    schema_path = Path(argv[2]) if len(argv) > 2 else SCHEMA
    spec = load(spec_path)
    schema = load(schema_path)

    errors = []
    schema_check(spec, schema, errors)
    consistency_check(spec, errors)

    if errors:
        print("INVALID: %d problem(s)" % len(errors))
        for err in errors:
            print("  - %s" % err)
        return 1
    print("OK: %s v%s (%d top-level sections)"
          % (spec["meta"]["id"], spec["meta"]["version"], len(spec) - 1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
