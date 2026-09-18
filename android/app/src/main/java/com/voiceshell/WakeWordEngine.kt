package com.voiceshell

import android.content.Context
import android.util.Log
import org.json.JSONObject
import org.vosk.LibVosk
import org.vosk.LogLevel
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.RecognitionListener
import org.vosk.android.SpeechService
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.zip.ZipInputStream

/**
 * Локальное распознавание обращения.
 *
 * Вынесено отдельным классом нарочно: нативная библиотека может не загрузиться
 * на конкретном устройстве, и тогда ошибка ловится при обращении к этому классу,
 * а приложение продолжает работать — просто без wake word.
 */
class WakeWordEngine(private val context: Context) {

    companion object {
        private const val MODEL_URL =
            "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"
        private const val MODEL_DIR = "vosk-model-small-ru-0.22"
        /** Метка «скачано целиком»: каталог появляется гораздо раньше. */
        private const val COMPLETE = ".complete"
        private const val TAG = "VoiceShell"
    }

    private var model: Model? = null
    private var speech: SpeechService? = null
    @Volatile private var failed = false

    /**
     * Работает ли модель на самом деле.
     *
     * Раньше здесь стояло `speech != null`, а поток распознавания vosk,
     * умерев от ошибки чтения микрофона, ссылку за собой не убирал. Флаг
     * продолжал говорить «работаю», и сторож раз в полминуты — единственная
     * страховка в этой конструкции — каждый раз проходил мимо. Телефон
     * переставал слышать обращение навсегда и молча.
     */
    val isRunning: Boolean get() = speech != null && !failed

    /**
     * Качает модель, если её нет. Бросает — вызывающий решает, что делать.
     *
     * Признак «скачано» — отдельный файл, а не существование каталога.
     * Каталог появляется на первой же записи из архива, и оборванная
     * закачка — вышел из зоны вайфая, сел в машину — оставляла его на месте
     * с половиной модели внутри. Дальше закачка пропускалась навсегда, а
     * `Model()` падал на каждом запуске: обращение не работало до
     * переустановки приложения, и чинилось это только ею.
     */
    fun prepare(onProgress: (String) -> Unit) {
        val dir = File(context.filesDir, MODEL_DIR)
        val done = File(dir, COMPLETE)
        if (!done.exists()) {
            runCatching { dir.deleteRecursively() }
            onProgress(context.getString(R.string.wake_downloading_model))
            download(MODEL_URL, context.filesDir)
            // Метку ставим последней: всё, что до неё, можно смело стирать.
            runCatching { done.writeText("ok") }
        }
        LibVosk.setLogLevel(LogLevel.WARNINGS)
        model = try {
            Model(dir.absolutePath)
        } catch (t: Throwable) {
            // Модель есть, но не читается: метку снимаем, чтобы следующий
            // запуск скачал заново, а не бился в то же самое.
            runCatching { done.delete() }
            throw t
        }
    }

    fun start(onText: (String) -> Unit, onError: (Throwable) -> Unit) {
        val ready = model ?: throw IllegalStateException("model is not prepared")
        stop()
        failed = false
        speech = SpeechService(Recognizer(ready, 16000.0f), 16000.0f)
        speech?.startListening(object : RecognitionListener {
            override fun onResult(hypothesis: String?) {
                val text = JSONObject(hypothesis ?: "{}").optString("text").trim()
                if (text.isNotEmpty()) onText(text)
            }

            override fun onFinalResult(hypothesis: String?) = Unit
            override fun onPartialResult(hypothesis: String?) = Unit
            override fun onTimeout() = Unit
            override fun onError(exception: Exception?) {
                Log.e(TAG, "vosk", exception)
                // Поток распознавания на этом кончается — значит и движок
                // больше не работает, что бы ни говорила ссылка на него.
                failed = true
                exception?.let(onError)
            }
        })
    }

    fun stop() {
        runCatching { speech?.stop() }
        runCatching { speech?.shutdown() }
        speech = null
    }

    fun release() {
        stop()
        runCatching { model?.close() }
        model = null
    }

    private fun download(url: String, target: File) {
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.connectTimeout = 30_000
        connection.readTimeout = 60_000
        ZipInputStream(connection.inputStream.buffered()).use { zip ->
            var entry = zip.nextEntry
            while (entry != null) {
                val file = File(target, entry.name)
                if (entry.isDirectory) {
                    file.mkdirs()
                } else {
                    file.parentFile?.mkdirs()
                    FileOutputStream(file).use { out -> zip.copyTo(out) }
                }
                entry = zip.nextEntry
            }
        }
        connection.disconnect()
    }
}
