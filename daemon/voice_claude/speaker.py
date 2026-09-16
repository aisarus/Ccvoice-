"""Speaker role classification: master / bystander / unknown / self_echo.

Implements spec section `speaker_identification`. Loudness is the primary
signal but never the only one: the decision is a weighted logistic over
z-normalised acoustic features, with hard gates in front and hysteresis on top.
Low confidence yields `unknown` rather than a guess.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

from .spec import section

ACOUSTIC_FEATURES = (
    "level_rel_db",
    "snr_db",
    "drr_db",
    "c50_db",
    "hf_ratio_db",
    "lf_proximity_db",
    "voiceprint_similarity",
)

# Pseudo-features: not measured from the signal, derived from context.
PRIOR_FEATURES = ("channel_prior", "continuity_prior")

CHANNEL_PRIOR = {"sony_mic": 1.0, "phone_mic": 0.5, "laptop_mic": -0.5}


@dataclass
class Features:
    """One speech segment, already measured on the device."""

    level_rel_db: float
    snr_db: float
    drr_db: float
    c50_db: float
    hf_ratio_db: float
    lf_proximity_db: float
    voiceprint_similarity: float | None = None

    def as_dict(self) -> dict[str, float | None]:
        return asdict(self)


@dataclass
class SegmentContext:
    """Everything about the segment that is not an acoustic feature."""

    device: str = "phone_mic"
    duration_ms: int = 1200
    voiced_frames: int = 40
    overlap: bool = False
    clipping_pct: float = 0.0
    echo_correlation: float = 0.0
    steady_level_s: float = 0.0
    narrowband: bool = False
    last_role: str | None = None
    ms_since_last: int = 10_000


@dataclass
class Decision:
    role: str
    p_master: float
    confidence: float
    profile: str
    gates: list[str] = field(default_factory=list)
    features_z: dict[str, float] = field(default_factory=dict)

    @property
    def label_ru(self) -> str:
        return {
            "master": "говорит мастер",
            "bystander": "говорит собеседник",
            "unknown": "неопределённый говорящий",
            "self_echo": "эхо собственного TTS",
        }[self.role]


@dataclass
class Calibration:
    """Per-device baseline. Enrollment seeds it, adaptive EMA keeps it fresh."""

    mu: dict[str, float]
    sigma: dict[str, float]
    ema_alpha: float = 0.05

    @classmethod
    def neutral(cls, ema_alpha: float = 0.05) -> "Calibration":
        # Baselines describe the master's own speech, so a master segment sits
        # near zero on every axis and anything further away is someone else.
        mu = {
            "level_rel_db": 0.0,
            "snr_db": 26.0,
            "drr_db": 8.0,
            "c50_db": 12.0,
            "hf_ratio_db": 0.0,
            "lf_proximity_db": 3.0,
            "voiceprint_similarity": 0.72,
        }
        sigma = {
            "level_rel_db": 4.5,
            "snr_db": 7.0,
            "drr_db": 4.0,
            "c50_db": 5.0,
            "hf_ratio_db": 4.0,
            "lf_proximity_db": 3.0,
            "voiceprint_similarity": 0.12,
        }
        return cls(mu=mu, sigma=sigma, ema_alpha=ema_alpha)

    def z(self, name: str, value: float) -> float:
        sigma = self.sigma.get(name) or 1.0
        return max(-3.0, min(3.0, (value - self.mu.get(name, 0.0)) / sigma))

    def update(self, features: Features) -> None:
        a = self.ema_alpha
        for name, value in features.as_dict().items():
            if value is None:
                continue
            self.mu[name] = (1 - a) * self.mu.get(name, value) + a * value


class SpeakerClassifier:
    """Stateful classifier: keeps calibration and the previous decision."""

    def __init__(self, profile: str | None = None, calibration: Calibration | None = None,
                 spec_section: dict[str, Any] | None = None) -> None:
        self._spec = spec_section or section("speaker_identification")
        self._profiles = {p["id"]: p for p in self._spec["scoring"]["weight_profiles"]}
        default_profile = next(p["id"] for p in self._spec["scoring"]["weight_profiles"] if p.get("default"))
        self.profile = profile or default_profile
        self.calibration = calibration or Calibration.neutral(
            self._spec["feature_normalization"]["ema_alpha"]
        )
        self._last: Decision | None = None
        self._last_ts: float = 0.0

    # -- scoring ---------------------------------------------------------
    def _weights(self, profile: str) -> dict[str, float]:
        return self._profiles[profile]["weights"]

    def _thresholds(self, ctx: SegmentContext) -> tuple[float, float]:
        th = self._spec["decision"]["thresholds"]
        hy = self._spec["decision"]["hysteresis"]
        master_min = th["master_min_p"]
        if ctx.last_role == "master" and ctx.ms_since_last <= hy["continuation_window_ms"]:
            master_min = hy["continuation_master_min_p"]
        elif ctx.last_role == "bystander" and ctx.ms_since_last <= hy["after_bystander_window_ms"]:
            master_min = hy["after_bystander_master_min_p"]
        return master_min, th["bystander_max_p"]

    def _hard_gates(self, ctx: SegmentContext) -> list[str]:
        gates: list[str] = []
        if ctx.echo_correlation > 0.6:
            gates.append("self_echo")
        if ctx.duration_ms < 400:
            gates.append("min_duration")
        if ctx.voiced_frames < 12:
            gates.append("min_voiced_frames")
        if ctx.overlap:
            gates.append("overlap")
        if ctx.clipping_pct > 2.0:
            gates.append("clipping")
        if ctx.steady_level_s > 8.0:
            gates.append("media_playback")
        return gates

    def classify(self, features: Features, ctx: SegmentContext | None = None,
                 profile: str | None = None) -> Decision:
        ctx = ctx or SegmentContext()
        if ctx.last_role is None and self._last is not None:
            ctx.last_role = self._last.role
            ctx.ms_since_last = int((time.monotonic() - self._last_ts) * 1000)

        gates = self._hard_gates(ctx)
        if "self_echo" in gates:
            return self._remember(Decision("self_echo", 0.0, 1.0, profile or self.profile, gates))

        z: dict[str, float] = {}
        unmeasured: list[str] = []
        for name in ACOUSTIC_FEATURES:
            value = getattr(features, name)
            if value is None:
                unmeasured.append(name)
                continue
            z[name] = self.calibration.z(name, value)
        z["channel_prior"] = CHANNEL_PRIOR.get(ctx.device, 0.0)
        z["continuity_prior"] = self._continuity(ctx)

        profile = self._select_profile(z, ctx, features, profile)
        weights = dict(self._weights(profile))
        for name in unmeasured:
            weights[name] = 0.0

        if "clipping" in gates:
            # The level is untrustworthy; drop that one weight, keep the rest.
            weights["level_rel_db"] = 0.0

        score = self._spec["scoring"]["bias_b0"]
        score += sum(weights.get(name, 0.0) * value for name, value in z.items())
        if "media_playback" in gates:
            score -= 0.8
        p_master = 1.0 / (1.0 + math.exp(-score))

        blocking = {"min_duration", "min_voiced_frames", "overlap"} & set(gates)
        if blocking:
            return self._remember(Decision("unknown", p_master, 0.0, profile, gates, z))

        master_min, bystander_max = self._thresholds(ctx)
        if p_master >= master_min:
            decision = Decision("master", p_master, p_master, profile, gates, z)
        elif p_master <= bystander_max:
            decision = Decision("bystander", p_master, 1.0 - p_master, profile, gates, z)
        else:
            decision = Decision("unknown", p_master, 0.0, profile, gates, z)

        if decision.role == "master" and p_master >= 0.9 and "clipping" not in gates:
            self.calibration.update(features)
        return self._remember(decision)

    def _select_profile(self, z: dict[str, float], ctx: SegmentContext,
                        features: Features, forced: str | None) -> str:
        """Pick the weight profile per segment (spec: scoring.profile_selection)."""
        if forced:
            profile = forced
        elif ctx.device == "sony_mic" and ctx.narrowband:
            return "narrowband"
        else:
            profile = self.profile

        # Loudness is a proxy for distance; when the direct distance evidence
        # (DRR, proximity effect) contradicts it, the direct evidence wins.
        quiet = z.get("level_rel_db", 0.0) < -1.0
        close = z.get("drr_db", 0.0) > 0.3 or z.get("lf_proximity_db", 0.0) > 0.3
        if quiet and close and "whisper" in self._profiles:
            return "whisper"

        if profile == "with_voiceprint" and features.voiceprint_similarity is None:
            return "acoustic_only"
        return profile

    def _continuity(self, ctx: SegmentContext) -> float:
        hy = self._spec["decision"]["hysteresis"]
        if ctx.last_role == "master" and ctx.ms_since_last <= hy["continuation_window_ms"]:
            return 1.0
        if ctx.last_role == "bystander" and ctx.ms_since_last <= hy["after_bystander_window_ms"]:
            return -1.0
        return 0.0

    def _remember(self, decision: Decision) -> Decision:
        if decision.role != "self_echo":
            self._last = decision
            self._last_ts = time.monotonic()
        return decision

    # -- policy ----------------------------------------------------------
    def may(self, action: str, decision: Decision) -> bool:
        """Policy gate: only master wakes, executes, barges in or approves."""
        key = {
            "execute": "roles_that_execute",
            "wake": "roles_that_can_wake",
            "barge_in": "roles_that_can_barge_in",
            "approve": "roles_that_can_answer_permission",
        }[action]
        if decision.role not in self._spec["policy"][key]:
            return False
        if action == "approve":
            return decision.confidence >= self._spec["policy"]["permission_min_confidence"]
        return True

    def role_line(self, decision: Decision, device: str) -> str:
        template = self._spec["claude_injection"]["templates"].get(decision.role)
        if template is None:  # self_echo never reaches Claude
            return ""
        return template.format(confidence=f"{decision.confidence:.2f}", device=device)


def debug_record(decision: Decision, features: Features, ctx: SegmentContext) -> dict[str, Any]:
    """One row for the local ring buffer used to tune thresholds. No audio."""
    return {
        "ts": time.time(),
        "device": ctx.device,
        "duration_ms": ctx.duration_ms,
        **{k: v for k, v in features.as_dict().items()},
        "p_master": round(decision.p_master, 4),
        "role": decision.role,
        "profile": decision.profile,
        "gates_triggered": decision.gates,
    }


def ring_buffer(records: Iterable[dict[str, Any]], limit: int = 200) -> list[dict[str, Any]]:
    items = list(records)
    return items[-limit:]
