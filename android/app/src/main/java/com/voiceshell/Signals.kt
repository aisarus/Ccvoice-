package com.voiceshell

import android.content.Context
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/**
 * Короткий сигнал вместо догадок.
 *
 * Голосовой интерфейс без обратной связи невозможно использовать: человек не
 * знает, открыт ли микрофон, и говорит в пустоту. Сигнал должен быть коротким,
 * различимым на слух и приходить туда же, куда идёт речь, — в наушники.
 *
 * Звук берём у системы, а не файлами: ToneGenerator есть всегда, ничего не
 * весит и не зависит от громкости музыки. Вибрация дублирует его для случая,
 * когда в ушах играет что-то своё.
 */
class Signals(private val context: Context) {

    private var tone: ToneGenerator? = null
    private var stream = AudioManager.STREAM_NOTIFICATION

    private val vibrator: Vibrator? by lazy {
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                context.getSystemService(VibratorManager::class.java).defaultVibrator
            } else {
                @Suppress("DEPRECATION")
                context.getSystemService(Vibrator::class.java)
            }
        }.getOrNull()
    }

    /** Микрофон открыт — говори. */
    fun listening(onCallChannel: Boolean) {
        beep(ToneGenerator.TONE_PROP_BEEP, 120, onCallChannel)
        buzz(40)
    }

    /** Реплика принята и ушла. */
    fun accepted(onCallChannel: Boolean) {
        beep(ToneGenerator.TONE_PROP_ACK, 150, onCallChannel)
        buzz(25)
    }

    /** Ничего не расслышал — микрофон закрылся впустую. */
    fun missed(onCallChannel: Boolean) {
        beep(ToneGenerator.TONE_PROP_NACK, 200, onCallChannel)
    }

    fun release() {
        runCatching { tone?.release() }
        tone = null
    }

    private fun beep(type: Int, ms: Int, onCallChannel: Boolean) {
        // Пока поднят канал гарнитуры, всё остальное уходит в динамик телефона.
        val wanted = if (onCallChannel) AudioManager.STREAM_VOICE_CALL
                     else AudioManager.STREAM_NOTIFICATION
        runCatching {
            if (tone == null || stream != wanted) {
                runCatching { tone?.release() }
                tone = ToneGenerator(wanted, 70)
                stream = wanted
            }
            tone?.startTone(type, ms)
        }
    }

    private fun buzz(ms: Long) {
        runCatching {
            vibrator?.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE))
        }
    }
}
