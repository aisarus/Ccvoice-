package com.voiceshell

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.Manifest
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.speech.RecognitionListener as CloudListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.support.v4.media.session.MediaSessionCompat
import android.support.v4.media.session.PlaybackStateCompat
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
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
import java.util.Locale
import java.util.concurrent.TimeUnit
import java.util.zip.ZipInputStream

/**
 * Фоновая служба: локальное распознавание, wake word, кнопка гарнитуры, связь с демоном.
 *
 * Модель Vosk работает на устройстве, поэтому постоянный микрофон не означает
 * постоянный поток аудио наружу — наружу уходит только текст принятой реплики.
 */
class VoiceService : Service(), RecognitionListener {

    companion object {
        const val ACTION_STATUS = "com.voiceshell.STATUS"
        const val ACTION_STOP = "com.voiceshell.STOP"
        const val EXTRA_TEXT = "text"
        private const val CHANNEL = "voice-shell"
        private const val NOTIFICATION_ID = 42
        private const val MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"
        private const val MODEL_DIR = "vosk-model-small-ru-0.22"
        private const val WINDOW_MS = 15_000L
        private const val TAG = "VoiceShell"
    }

    private lateinit var prefs: Prefs
    private val main = Handler(Looper.getMainLooper())
    private val http = OkHttpClient.Builder().pingInterval(20, TimeUnit.SECONDS).build()

    private var socket: WebSocket? = null
    private var speech: SpeechService? = null
    private var model: Model? = null
    private var tts: TextToSpeech? = null
    private var session: MediaSessionCompat? = null

    private var speaking = false
    private var windowUntil = 0L
    private var status = "запуск"
    private var cloud: SpeechRecognizer? = null
    private var awaitingCommand = false

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            fail("нет разрешения на микрофон — выдай его и запусти снова")
            return
        }

        try {
            createChannel()
            // На Android 14 тип обязателен, иначе служба падает с исключением.
            ServiceCompat.startForeground(
                this, NOTIFICATION_ID, notification("запуск"),
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
            )
        } catch (e: Exception) {
            fail("служба не смогла стартовать: ${e.javaClass.simpleName}: ${e.message}")
            return
        }

        try {
            setUpTts()
            setUpMediaSession()
            connect()
            Thread { prepareModel() }.start()
            prefs.lastError = ""
        } catch (e: Exception) {
            fail("ошибка при запуске: ${e.javaClass.simpleName}: ${e.message}")
        }
    }

    /** Сообщить причину и остановиться, а не падать молча. */
    private fun fail(reason: String) {
        Log.e(TAG, reason)
        runCatching { prefs.lastError = reason }
        sendBroadcast(Intent(ACTION_STATUS).setPackage(packageName).putExtra(EXTRA_TEXT, reason))
        stopSelf()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        return START_STICKY
    }

    override fun onDestroy() {
        cloud?.destroy()
        speech?.stop()
        speech?.shutdown()
        model?.close()
        tts?.shutdown()
        session?.release()
        socket?.close(1000, "service stopped")
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ---------- распознавание ----------
    private fun prepareModel() {
        try {
            val dir = File(filesDir, MODEL_DIR)
            if (!dir.exists()) {
                report("качаю модель распознавания, ~45 МБ")
                download(MODEL_URL, filesDir)
            }
            LibVosk.setLogLevel(LogLevel.WARNINGS)
            model = Model(dir.absolutePath)
            main.post { startListening() }
        } catch (e: Exception) {
            Log.e(TAG, "model", e)
            report("не удалось подготовить модель: ${e.message}")
        }
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

    private fun startListening() {
        val ready = model ?: return
        try {
            speech?.stop()
            speech = SpeechService(Recognizer(ready, 16000.0f), 16000.0f)
            speech?.startListening(this)
            report("слушаю — скажи «Клод…»")
        } catch (e: Exception) {
            Log.e(TAG, "listen", e)
            report("микрофон недоступен: ${e.message}")
        }
    }

    override fun onResult(hypothesis: String?) {
        val text = JSONObject(hypothesis ?: "{}").optString("text").trim()
        if (text.isEmpty()) return
        handle(text)
    }

    override fun onFinalResult(hypothesis: String?) = Unit
    override fun onPartialResult(hypothesis: String?) = Unit
    override fun onTimeout() = Unit
    override fun onError(exception: Exception?) {
        Log.e(TAG, "vosk", exception)
        report("ошибка распознавания: ${exception?.message}")
    }

    private fun handle(text: String) {
        Intents.stopIntent(text)?.let { scope ->
            silence()
            send(JSONObject().put("id", "interrupt").put("scope", scope))
            report(if (scope == "work") "останавливаю работу" else "тихо")
            return
        }
        Intents.languageSwitch(text)?.let { code ->
            prefs.language = code
            val said = mapOf("ru-RU" to "Говорю по-русски.", "en-US" to "Switching to English.",
                             "he-IL" to "עובר לעברית.")[code].orEmpty()
            report("язык: $code")
            speak(said)
            return
        }
        if (speaking) return                       // во время ответа слышно самих себя
        val windowOpen = System.currentTimeMillis() < windowUntil
        if (!windowOpen && !Intents.hasWake(text)) return
        windowUntil = 0

        val payload = Intents.stripWake(text)
        // Обращение прозвучало, но команда — нет: слушаем её отдельно, на выбранном языке.
        if (payload.isBlank() || Intents.hasWake(text) && payload == Intents.normalise(text)) {
            listenForCommand()
            return
        }
        if (prefs.language != "ru-RU") {
            // Локальная модель русская: саму реплику распознаём на нужном языке.
            listenForCommand()
            return
        }
        deliver(payload)
    }

    private fun deliver(payload: String) {
        if (payload.isBlank()) return
        report("→ $payload")
        send(
            JSONObject()
                .put("id", "speech_segment")
                .put("segment_id", System.currentTimeMillis().toString())
                .put("transcript", payload)
                .put("device", "phone_mic")
                .put("duration_ms", 1200)
                .put("voiced_frames", 40)
                .put("role", "master")
        )
    }

    /**
     * Реплика на выбранном языке.
     *
     * Wake word ловится локально, поэтому наружу уходит только то, что сказано
     * после обращения — зато распознаётся точнее и на любом языке, включая иврит,
     * для которого локальной модели нет.
     */
    private fun listenForCommand() {
        if (awaitingCommand) return
        awaitingCommand = true
        main.post {
            speech?.stop()
            if (cloud == null) cloud = SpeechRecognizer.createSpeechRecognizer(this)
            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, prefs.language)
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                // Android 14 умеет переключать язык сам; на старых — просто игнорируется.
                putExtra("android.speech.extra.ENABLE_LANGUAGE_SWITCH", "adaptive")
                putStringArrayListExtra(
                    "android.speech.extra.LANGUAGE_SWITCH_ALLOWED_LANGUAGES",
                    arrayListOf("ru-RU", "en-US", "he-IL")
                )
            }
            cloud?.setRecognitionListener(object : CloudListener {
                override fun onResults(results: Bundle?) {
                    val text = results
                        ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        ?.firstOrNull()
                        ?.trim()
                        .orEmpty()
                    finishCommand()
                    if (text.isEmpty()) return
                    Intents.stopIntent(text)?.let { scope ->
                        silence()
                        send(JSONObject().put("id", "interrupt").put("scope", scope))
                        return
                    }
                    deliver(text)
                }

                override fun onError(error: Int) {
                    finishCommand()
                    if (error != SpeechRecognizer.ERROR_NO_MATCH &&
                        error != SpeechRecognizer.ERROR_SPEECH_TIMEOUT
                    ) {
                        report("распознавание реплики: ошибка $error")
                    }
                }

                override fun onReadyForSpeech(params: Bundle?) { report("слушаю реплику…") }
                override fun onBeginningOfSpeech() = Unit
                override fun onRmsChanged(rmsdB: Float) = Unit
                override fun onBufferReceived(buffer: ByteArray?) = Unit
                override fun onEndOfSpeech() = Unit
                override fun onPartialResults(partialResults: Bundle?) = Unit
                override fun onEvent(eventType: Int, params: Bundle?) = Unit
            })
            cloud?.startListening(intent)
        }
    }

    private fun finishCommand() {
        awaitingCommand = false
        main.postDelayed({ if (!awaitingCommand) startListening() }, 300)
    }

    // ---------- кнопка гарнитуры ----------
    private fun setUpMediaSession() {
        session = MediaSessionCompat(this, "VoiceShell").apply {
            setPlaybackState(
                PlaybackStateCompat.Builder()
                    .setActions(
                        PlaybackStateCompat.ACTION_PLAY or PlaybackStateCompat.ACTION_PAUSE or
                            PlaybackStateCompat.ACTION_PLAY_PAUSE or PlaybackStateCompat.ACTION_STOP
                    )
                    .setState(PlaybackStateCompat.STATE_PLAYING, 0, 1.0f)
                    .build()
            )
            setCallback(object : MediaSessionCompat.Callback() {
                override fun onPlay() = armWindow()
                override fun onPause() = armWindow()
                override fun onStop() = armWindow()
                override fun onSkipToNext() = armWindow()
                override fun onSkipToPrevious() = armWindow()
            })
            isActive = true
        }
    }

    /** Кнопка не включает микрофон — он уже слушает, — она разрешает реплику без обращения. */
    private fun armWindow() {
        if (speaking) silence()
        windowUntil = System.currentTimeMillis() + WINDOW_MS
        report("слушаю — говори")
        if (prefs.language != "ru-RU") listenForCommand()
    }

    // ---------- речь ----------
    private fun setUpTts() {
        tts = TextToSpeech(this) { code ->
            if (code == TextToSpeech.SUCCESS) {
                tts?.language = Locale.forLanguageTag(prefs.language)
                tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String?) { speaking = true }
                    override fun onDone(utteranceId: String?) {
                        speaking = false
                        windowUntil = System.currentTimeMillis() + WINDOW_MS
                    }
                    @Deprecated("deprecated in API 21")
                    override fun onError(utteranceId: String?) { speaking = false }
                })
            }
        }
    }

    /** Язык ответа берётся из самого ответа: русский текст — русский голос. */
    private fun speak(text: String) {
        val code = Intents.scriptLanguage(text, prefs.language)
        tts?.language = Locale.forLanguageTag(code)
        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "voice-shell")
    }

    private fun silence() {
        tts?.stop()
        speaking = false
    }

    // ---------- связь ----------
    private fun connect() {
        if (!prefs.isConfigured) {
            report("не настроено: укажи адрес и токен")
            return
        }
        val request = Request.Builder().url(prefs.socketUrl()).build()
        socket = http.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(
                    JSONObject().put("id", "hello").put("v", 1)
                        .put("token", prefs.token).put("device_id", "android")
                        .put("app_version", "0.3.0").toString()
                )
                report("подключено")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                main.post { onServerMessage(JSONObject(text)) }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                report("нет связи: ${t.message}")
                main.postDelayed({ connect() }, 4000)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                main.postDelayed({ connect() }, 2000)
            }
        })
    }

    private fun onServerMessage(message: JSONObject) {
        when (message.optString("id")) {
            "welcome" -> report("готов · ${message.optString("credential")}")
            "voice_summary" -> {
                val text = message.optString("text")
                report(text)
                speak(text)
            }
            "permission_request" -> {
                val spoken = message.optString("spoken")
                report(spoken)
                speak(spoken)
            }
            "whisper" -> speak(message.optString("text"))
            "error" -> report("ошибка: ${message.optString("message")}")
        }
    }

    private fun send(payload: JSONObject) {
        socket?.send(payload.toString()) ?: report("нет связи")
    }

    // ---------- уведомление и статус ----------
    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(CHANNEL, "Voice Shell", NotificationManager.IMPORTANCE_LOW)
            channel.setShowBadge(false)
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }

    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, VoiceService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Voice Shell")
            .setContentText(text)
            .setContentIntent(open)
            .addAction(0, "Стоп", stop)
            .setOngoing(true)
            .setSilent(true)
            .build()
    }

    private fun report(text: String) {
        status = text
        if (text.startsWith("не ") || text.contains("ошибка")) runCatching { prefs.lastError = text }
        main.post {
            getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification(text))
            sendBroadcast(Intent(ACTION_STATUS).setPackage(packageName).putExtra(EXTRA_TEXT, text))
        }
    }
}
