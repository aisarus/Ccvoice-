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
        private const val TAG = "VoiceShell"
    }

    private var model: Model? = null
    private var speech: SpeechService? = null

    val isRunning: Boolean get() = speech != null

    /** Качает модель, если её нет. Бросает — вызывающий решает, что делать. */
    fun prepare(onProgress: (String) -> Unit) {
        val dir = File(context.filesDir, MODEL_DIR)
        if (!dir.exists()) {
            onProgress("качаю модель распознавания, ~45 МБ")
            download(MODEL_URL, context.filesDir)
        }
        LibVosk.setLogLevel(LogLevel.WARNINGS)
        model = Model(dir.absolutePath)
    }

    fun start(onText: (String) -> Unit, onError: (Throwable) -> Unit) {
        val ready = model ?: throw IllegalStateException("модель не подготовлена")
        stop()
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
