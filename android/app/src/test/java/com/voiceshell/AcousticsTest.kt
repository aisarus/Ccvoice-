package com.voiceshell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Приложение годами объявляло себя мастером без единого измерения, и защита от
 * чужого голоса существовала только в спеке. Здесь проверяется главное свойство
 * измерений: они либо честные, либо их нет вовсе.
 */
class AcousticsTest {

    /** Реплика: сначала тишина комнаты, потом речь. Кадры каждые 100 мс. */
    private fun utterance(
        noise: Float, speech: Float, quietFrames: Int = 10, loudFrames: Int = 12,
    ): Segment {
        val meter = SpeechMeter()
        var at = 1_000L
        repeat(quietFrames) { meter.frame(noise, at); at += 100 }
        meter.speechBegan(at)
        repeat(loudFrames) { meter.frame(speech, at); at += 100 }
        meter.speechEnded(at)
        return meter.segment() ?: throw AssertionError("реплику не удалось измерить")
    }

    @Test
    fun theFirstUtteranceOnAMicrophoneClaimsNothingAndOnlyCalibrates() {
        val segment = utterance(noise = -2f, speech = 8f)
        // Нормы ещё нет, сравнивать не с чем — и «ноль» здесь был бы враньём:
        // он сказал бы демону «говорили ровно как обычно».
        assertTrue(Acoustics.features(segment, null).isEmpty())
        assertNotNull(Acoustics.nextBaseline(segment, null))
    }

    @Test
    fun speechAtTheUsualLoudnessLooksNormalToTheDaemon() {
        val first = utterance(noise = -2f, speech = 8f)
        val baseline = Acoustics.nextBaseline(first, null)
        val features = Acoustics.features(utterance(noise = -2f, speech = 8f), baseline)
        // Ровно норма: уровень относительно неё — ноль, а SNR — то число,
        // которое демон считает нормой мастера.
        assertEquals(0.0, features.getValue("level_rel_db"), 0.2)
        assertEquals(Acoustics.TYPICAL_MASTER_SNR_DB, features.getValue("snr_db"), 0.2)
    }

    @Test
    fun speechFromAcrossTheRoomIsQuieterAndCloserToTheNoise() {
        val baseline = Acoustics.nextBaseline(utterance(noise = -2f, speech = 8f), null)
        val far = Acoustics.features(utterance(noise = 0f, speech = 3f), baseline)
        assertTrue("уровень: ${far["level_rel_db"]}", far.getValue("level_rel_db") < -8.0)
        assertTrue("snr: ${far["snr_db"]}", far.getValue("snr_db") < 12.0)
    }

    @Test
    fun onlyTheTwoMeasuredFeaturesAreSent() {
        val baseline = Acoustics.nextBaseline(utterance(noise = -2f, speech = 8f), null)
        val features = Acoustics.features(utterance(noise = -2f, speech = 7f), baseline)
        // Остальные признаки спеки телефон измерить не может. Демон обнуляет
        // вес того, чего нет, — и это лучше выдуманного числа.
        assertEquals(setOf("level_rel_db", "snr_db"), features.keys)
    }

    @Test
    fun aRoomWithoutSpeechIsNotMeasuredAtAll() {
        val meter = SpeechMeter()
        var at = 0L
        // Ровный фон: кондиционер, улица, телевизор за стеной.
        repeat(30) { meter.frame(if (it % 2 == 0) -2.0f else -1.6f, at); at += 100 }
        assertNull(meter.segment())
    }

    @Test
    fun aHandfulOfFramesIsNotEnoughToClaimAnything() {
        val meter = SpeechMeter()
        var at = 0L
        repeat(4) { meter.frame(if (it < 2) -2f else 9f, at); at += 100 }
        assertNull(meter.segment())
    }

    @Test
    fun voicedFramesStayAboveTheDaemonsGateForARealReply() {
        // Полторы секунды речи — 1200 мс громких кадров. Гейт демона — 12
        // кадров; если бы кадр считался по частоте вызовов распознавателя,
        // реплика уезжала бы в unknown на телефоне с редкими вызовами.
        val segment = utterance(noise = -2f, speech = 8f)
        assertEquals(1200 / Acoustics.FRAME_MS, Acoustics.voicedFrames(segment))
        assertTrue(Acoustics.voicedFrames(segment) > 12)
    }

    @Test
    fun theDurationIsTheSpeechSegmentNotTheWholeListeningWindow() {
        val segment = utterance(noise = -2f, speech = 8f)
        // Секунда тишины перед репликой в длительность не попадает: иначе
        // короткий выкрик соседа выглядел бы как долгая речь.
        assertEquals(1200, segment.durationMs)
    }

    @Test
    fun aQuietReplyDoesNotDragTheBaselineDown() {
        val baseline = Acoustics.nextBaseline(utterance(noise = -2f, speech = 8f), null)
        val moved = Acoustics.nextBaseline(utterance(noise = 0f, speech = 3f), baseline)
        // Иначе одна тихая фраза опустила бы норму, следующая такая же
        // выглядела бы обычной — и различать своё и чужое стало бы нечем.
        assertEquals(baseline.speech, moved.speech, 0.001)
        val stillFar = Acoustics.features(utterance(noise = 0f, speech = 3f), moved)
        assertTrue(stillFar.getValue("level_rel_db") < -8.0)
    }

    @Test
    fun speakingLouderMovesTheBaselineUp() {
        val baseline = Acoustics.nextBaseline(utterance(noise = -2f, speech = 8f), null)
        val moved = Acoustics.nextBaseline(utterance(noise = -2f, speech = 12f), baseline)
        assertTrue(moved.speech > baseline.speech)
        assertTrue(moved.speech < 12.0)
    }

    @Test
    fun aBrokenScaleNeverProducesAbsurdNumbers() {
        // Движок отдаёт NaN на старте и вообще другую шкалу: признаки должны
        // остаться в разумных границах, а не уехать в бесконечность.
        val meter = SpeechMeter()
        var at = 0L
        meter.frame(Float.NaN, at)
        repeat(10) { meter.frame(-60f, at); at += 100 }
        repeat(10) { meter.frame(-12f, at); at += 100 }
        val segment = meter.segment() ?: throw AssertionError("не измерилось")
        val baseline = Acoustics.nextBaseline(segment, null)
        val features = Acoustics.features(utterance(noise = -60f, speech = -40f), baseline)
        assertTrue(features.getValue("snr_db") in 0.0..45.0)
        assertTrue(features.getValue("level_rel_db") in -40.0..12.0)
    }
}
