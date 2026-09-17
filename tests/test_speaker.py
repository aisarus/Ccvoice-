"""Speaker role classification — the cases the spec's acceptance criteria name."""
import pytest

from voice_claude.speaker import (Calibration, Decision, Features, SegmentContext,
                                  SpeakerClassifier)


def close_speech(**over):
    base = dict(level_rel_db=1.0, snr_db=30.0, drr_db=10.0, c50_db=14.0,
                hf_ratio_db=1.0, lf_proximity_db=4.0)
    base.update(over)
    return Features(**base)


def far_speech(**over):
    base = dict(level_rel_db=-14.0, snr_db=9.0, drr_db=-1.0, c50_db=2.0,
                hf_ratio_db=-7.0, lf_proximity_db=-2.0)
    base.update(over)
    return Features(**base)


@pytest.fixture
def classifier():
    return SpeakerClassifier(calibration=Calibration.neutral())


def test_close_clean_speech_is_master(classifier):
    d = classifier.classify(close_speech(), SegmentContext(device="phone_mic"))
    assert d.role == "master"
    assert d.confidence > 0.72


def test_distant_reverberant_speech_is_bystander(classifier):
    d = classifier.classify(far_speech(), SegmentContext(device="phone_mic"))
    assert d.role == "bystander"


def test_quiet_master_close_to_mic_is_not_bystander(classifier):
    """SI-6: whispering at 15 cm — low level but high DRR and proximity."""
    whisper = close_speech(level_rel_db=-11.0, snr_db=17.0, drr_db=11.0, lf_proximity_db=5.0)
    d = classifier.classify(whisper, SegmentContext(device="phone_mic"))
    assert d.role in {"master", "unknown"}
    assert d.role != "bystander"


def test_loud_bystander_nearby_is_not_master(classifier):
    """A close, loud stranger must not pass on loudness alone."""
    loud_stranger = Features(level_rel_db=2.0, snr_db=26.0, drr_db=1.0, c50_db=3.0,
                             hf_ratio_db=-5.0, lf_proximity_db=-3.0)
    d = classifier.classify(loud_stranger, SegmentContext(device="phone_mic"))
    assert d.role != "master"


def test_self_echo_is_gated_before_scoring(classifier):
    d = classifier.classify(close_speech(), SegmentContext(echo_correlation=0.9))
    assert d.role == "self_echo"
    assert "self_echo" in d.gates


def test_short_segment_is_unknown_not_a_guess(classifier):
    d = classifier.classify(close_speech(), SegmentContext(duration_ms=200, voiced_frames=5))
    assert d.role == "unknown"
    assert d.confidence == 0.0


def test_overlapping_speech_is_unknown(classifier):
    d = classifier.classify(close_speech(), SegmentContext(overlap=True))
    assert d.role == "unknown"


def test_clipping_drops_the_level_weight_only(classifier):
    d = classifier.classify(close_speech(level_rel_db=25.0), SegmentContext(clipping_pct=9.0))
    assert "clipping" in d.gates
    assert d.features_z["level_rel_db"] == pytest.approx(3.0)  # clipped, then unweighted


def test_steady_media_playback_is_penalised(classifier):
    d = classifier.classify(close_speech(), SegmentContext(steady_level_s=20.0))
    assert "media_playback" in d.gates


def test_hysteresis_keeps_a_continuing_master_segment(classifier):
    borderline = close_speech(level_rel_db=-6.0, snr_db=22.0, drr_db=5.0, c50_db=9.0,
                              hf_ratio_db=-2.0, lf_proximity_db=1.0)
    cold = classifier.classify(borderline, SegmentContext(last_role=None, ms_since_last=10 ** 6))
    warm = classifier.classify(borderline, SegmentContext(last_role="master", ms_since_last=800))
    assert warm.p_master > cold.p_master


def test_policy_only_master_executes_and_approves(classifier):
    master = classifier.classify(close_speech(), SegmentContext())
    bystander = classifier.classify(far_speech(), SegmentContext())
    assert classifier.may("execute", master)
    assert classifier.may("wake", master)
    assert not classifier.may("execute", bystander)
    assert not classifier.may("approve", bystander)
    assert not classifier.may("approve", Decision("master", 0.8, 0.8, "acoustic_only"))


def test_role_line_matches_spec_template(classifier):
    master = classifier.classify(close_speech(), SegmentContext())
    line = classifier.role_line(master, "phone_mic")
    assert "speaker=master" in line and "говорит мастер" in line and "phone_mic" in line


def test_self_echo_never_gets_a_role_line(classifier):
    echo = classifier.classify(close_speech(), SegmentContext(echo_correlation=0.95))
    assert classifier.role_line(echo, "phone_mic") == ""


def test_voiceprint_profile_falls_back_without_enrollment(classifier):
    c = SpeakerClassifier(profile="with_voiceprint", calibration=Calibration.neutral())
    d = c.classify(close_speech(), SegmentContext())
    assert d.profile == "acoustic_only"


def test_two_measured_signals_are_enough_to_recognise_the_master():
    """Телефон меряет два признака из шести. Профиль весов рассчитан на
    полный набор, и без пересчёта безупречная реплика хозяина не дотягивала
    до порога — то есть команда молча не исполнялась."""
    classifier = SpeakerClassifier()
    context = SegmentContext(device="phone_mic", duration_ms=1400, voiced_frames=45)
    decision = classifier.classify(Features(level_rel_db=1.0, snr_db=30.0), context)
    assert decision.role == "master", decision


def test_the_same_two_signals_still_catch_a_bystander():
    """Пересчёт не должен делать всех хозяевами."""
    classifier = SpeakerClassifier()
    context = SegmentContext(device="phone_mic", duration_ms=1400, voiced_frames=45)
    decision = classifier.classify(Features(level_rel_db=-14.0, snr_db=9.0), context)
    assert decision.role == "bystander", decision


def test_one_signal_is_not_a_verdict():
    """Одного признака мало: лучше «не знаю», чем уверенная ошибка."""
    classifier = SpeakerClassifier()
    context = SegmentContext(device="phone_mic", duration_ms=1400, voiced_frames=45)
    decision = classifier.classify(Features(level_rel_db=1.0), context)
    assert decision.role == "unknown"
    assert decision.confidence == 0.0


def test_a_missing_feature_is_not_a_bad_measurement():
    """Прежде демон подставлял «разумные» числа за телефон, и молчание
    превращалось в измеренный плохой результат."""
    classifier = SpeakerClassifier()
    context = SegmentContext(device="phone_mic", duration_ms=1400, voiced_frames=45)
    неполные = classifier.classify(Features(level_rel_db=1.0, snr_db=30.0), context)
    classifier._last = None
    полные = classifier.classify(
        Features(level_rel_db=1.0, snr_db=30.0, drr_db=10.0, c50_db=14.0,
                 hf_ratio_db=1.0, lf_proximity_db=4.0), context)
    assert неполные.role == полные.role == "master"
    # Неполный набор не должен звучать увереннее полного.
    assert неполные.p_master <= полные.p_master
