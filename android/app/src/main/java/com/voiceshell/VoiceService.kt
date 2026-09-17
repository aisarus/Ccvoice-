package com.voiceshell

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioDeviceInfo
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
import android.speech.tts.Voice
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
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Фоновая служба: связь с демоном, речь, кнопка гарнитуры и — если получится —
 * локальный wake word.
 *
 * Wake word держится отдельно и намеренно необязателен: нативная библиотека
 * может не подняться на конкретном устройстве, и это не повод ронять всё
 * остальное. Тогда реплика начинается кнопкой.
 */
class VoiceService : Service() {

    companion object {
        const val ACTION_STATUS = "com.voiceshell.STATUS"
        const val ACTION_STOP = "com.voiceshell.STOP"
        const val ACTION_LISTEN = "com.voiceshell.LISTEN"
        const val ACTION_AUTH_START = "com.voiceshell.AUTH_START"
        const val ACTION_AUTH_CODE = "com.voiceshell.AUTH_CODE"
        const val ACTION_SAY = "com.voiceshell.SAY"
        const val ACTION_MIC = "com.voiceshell.MIC"
        const val ACTION_AUTH_SET = "com.voiceshell.AUTH_SET"
        const val ACTION_VOICES = "com.voiceshell.VOICES"
        const val ACTION_ENGINES = "com.voiceshell.ENGINES"
        const val ACTION_SET_ENGINE = "com.voiceshell.SET_ENGINE"
        const val EXTRA_ENGINES = "engines"
        const val ACTION_TRY_VOICE = "com.voiceshell.TRY_VOICE"
        const val EXTRA_VOICES = "voices"
        const val EXTRA_TEXT = "text"
        const val EXTRA_CODE = "code"
        const val EXTRA_AUTH_URL = "auth_url"
        const val EXTRA_AUTH_TOKEN = "auth_token"
        private const val CHANNEL = "voice-shell"
        private const val NOTIFICATION_ID = 42
        private const val WINDOW_MS = 15_000L
        /** Столько ждём, что человек начнёт говорить, прежде чем взять расслышанное. */
        private const val FALLBACK_MS = 3_500L
        private const val TAG = "VoiceShell"
    }

    private lateinit var prefs: Prefs
    private val main = Handler(Looper.getMainLooper())
    private val http = OkHttpClient.Builder().pingInterval(20, TimeUnit.SECONDS).build()

    private var socket: WebSocket? = null
    private var tts: TextToSpeech? = null
    private var session: MediaSessionCompat? = null
    private var cloud: SpeechRecognizer? = null
    private var wake: WakeWordEngine? = null
    private var route: AudioRoute? = null

    private var speaking = false
    private var awaitingCommand = false
    private var lastSpoken = ""
    private var spokeAt = 0L
    private var windowUntil = 0L
    private var wakeReady = false
    private var fallbackText = ""
    private var unauthorized = false

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
            ServiceCompat.startForeground(
                this, NOTIFICATION_ID, notification("запуск"),
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
            )
        } catch (t: Throwable) {
            fail("служба не смогла стартовать: ${t.javaClass.simpleName}: ${t.message}")
            return
        }

        try {
            setUpTts()
            setUpMediaSession()
            connect()
            prefs.lastError = ""
        } catch (t: Throwable) {
            fail("ошибка при запуске: ${t.javaClass.simpleName}: ${t.message}")
            return
        }

        // Маршрут поднимаем до wake word: поток записи не переезжает на другое
        // устройство сам, и открывать его надо уже на нужном микрофоне.
        route = AudioRoute(
            this,
            onChanged = { why -> main.post { onRouteChanged(why) } },
            log = { line -> report(line) }
        )
        runCatching { route?.start(prefs.btMic) }
        report(route?.describe().orEmpty().ifBlank { "микрофон: телефон" })

        Thread { prepareWakeWord() }.start()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> { stopSelf(); return START_NOT_STICKY }
            ACTION_LISTEN -> armWindow()
            ACTION_MIC -> {
                route?.enable(prefs.btMic)
                report(route?.describe().orEmpty().ifBlank { "микрофон: телефон" })
            }
            ACTION_SAY -> {
                val text = intent.getStringExtra(EXTRA_TEXT).orEmpty().trim()
                if (text.isNotEmpty()) deliver(text)
            }
            ACTION_ENGINES -> {
                val engines = runCatching { tts?.engines.orEmpty() }.getOrDefault(emptyList())
                sendBroadcast(
                    Intent(ACTION_STATUS).setPackage(packageName)
                        .putExtra(EXTRA_TEXT, "движков синтеза: ${engines.size}")
                        .putStringArrayListExtra(
                            EXTRA_ENGINES,
                            ArrayList(engines.map { "${it.label}\u0000${it.name}" })
                        )
                )
            }
            ACTION_SET_ENGINE -> {
                val engine = intent.getStringExtra(EXTRA_CODE).orEmpty()
                prefs.engine = engine
                prefs.voice = ""            // голоса у другого движка свои
                runCatching { tts?.shutdown() }
                tts = null
                setUpTts()
                report("движок синтеза: ${engine.ifBlank { "системный" }}")
            }
            ACTION_VOICES -> {
                val names = runCatching { tts?.voices.orEmpty() }.getOrDefault(emptySet())
                    .filter { it.locale.language == Locale.forLanguageTag(prefs.language).language }
                    .sortedByDescending { it.quality }
                    .map { it.name }
                sendBroadcast(
                    Intent(ACTION_STATUS).setPackage(packageName)
                        .putExtra(EXTRA_TEXT, "голосов доступно: ${names.size}")
                        .putStringArrayListExtra(EXTRA_VOICES, ArrayList(names))
                )
            }
            ACTION_TRY_VOICE -> {
                val name = intent.getStringExtra(EXTRA_CODE).orEmpty()
                if (name.isNotBlank()) {
                    prefs.voice = name
                    applyVoice(prefs.language)
                    val sample = mapOf(
                        "ru-RU" to "Готово. Все сорок семь тестов проходят.",
                        "en-US" to "Done. All forty seven tests pass.",
                        "he-IL" to "מוכן. כל הבדיקות עוברות."
                    )[prefs.language].orEmpty()
                    tts?.speak(sample, TextToSpeech.QUEUE_FLUSH, speechParams(), "voice-shell-sample")
                }
            }
            ACTION_AUTH_SET -> {
                val token = intent.getStringExtra(EXTRA_CODE).orEmpty().trim()
                if (token.isNotEmpty()) {
                    report("проверяю токен Claude…")
                    send(JSONObject().put("id", "auth_set").put("token", token))
                }
            }
            ACTION_AUTH_START -> {
                report("запрашиваю ссылку авторизации…")
                send(JSONObject().put("id", "auth_start"))
            }
            ACTION_AUTH_CODE -> {
                val code = intent.getStringExtra(EXTRA_CODE).orEmpty().trim()
                if (code.isNotEmpty()) {
                    report("проверяю код…")
                    send(JSONObject().put("id", "auth_code").put("code", code))
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        runCatching { route?.release() }
        runCatching { wake?.release() }
        runCatching { cloud?.destroy() }
        runCatching { tts?.shutdown() }
        runCatching { session?.release() }
        runCatching { socket?.close(1000, "service stopped") }
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ---------- wake word (необязательный) ----------
    private fun prepareWakeWord() {
        try {
            val engine = WakeWordEngine(this)
            engine.prepare { report(it) }
            engine.start(
                onText = { text -> main.post { handle(text) } },
                onError = { t -> report("распознавание обращения: ${t.message}") }
            )
            wake = engine
            wakeReady = true
            report("слушаю — скажи «Клод…»")
        } catch (t: Throwable) {
            // Самый частый случай: не поднялась нативная библиотека.
            Log.e(TAG, "wake word", t)
            wakeReady = false
            report("wake word недоступен (${t.javaClass.simpleName}) — начинай реплику кнопкой")
        }
    }

    /**
     * Маршрут микрофона сменился: гарнитуру надели, сняли или канал переоткрыли.
     * Уже открытый поток записи остался на старом устройстве — перезапускаем.
     */
    private fun onRouteChanged(why: String) {
        Log.i(TAG, "маршрут: $why")
        if (!wakeReady || speaking || awaitingCommand) return
        runCatching { wake?.stop() }
        resumeWake()
    }

    // ---------- разбор реплики ----------
    private fun handle(text: String) {
        if (isOwnEcho(text)) return
        Intents.stopIntent(text)?.let { scope ->
            silence()
            send(JSONObject().put("id", "interrupt").put("scope", scope))
            report(if (scope == "work") "останавливаю работу" else "тихо")
            return
        }
        Intents.languageSwitch(text)?.let { code ->
            prefs.language = code
            report("язык: $code")
            speak(mapOf("ru-RU" to "Говорю по-русски.", "en-US" to "Switching to English.",
                        "he-IL" to "עובר לעברית.")[code].orEmpty())
            return
        }
        if (speaking) return
        val windowOpen = System.currentTimeMillis() < windowUntil
        if (!windowOpen && !Intents.hasWake(text)) return
        windowUntil = 0

        // Модель обращения — самая маленькая в системе: её дело услышать
        // «Клод» и «стоп», а не переписывать команду. Поэтому саму реплику
        // всегда отдаём нормальному распознавателю, а расслышанное держим
        // запасным вариантом: если человек сказал всё одним куском, повторять
        // ему уже нечего, и лучше отправить хотя бы это.
        val payload = Intents.stripWake(text)
        listenForCommand(if (prefs.language == "ru-RU") payload else "")
    }

    /**
     * Распознали собственный ответ.
     *
     * Через динамик это случается всегда, через наушники — когда звук утекает.
     * Сравниваем со сказанным: совпадающие куски — эхо, а не реплика.
     */
    private fun isOwnEcho(text: String): Boolean {
        if (lastSpoken.isBlank()) return false
        if (System.currentTimeMillis() - spokeAt > 12_000) return false
        val heard = Intents.normalise(text)
        if (heard.length < 4) return false
        if (lastSpoken.contains(heard) || heard.contains(lastSpoken)) return true
        val heardWords = heard.split(' ').filter { it.length > 3 }.toSet()
        if (heardWords.isEmpty()) return false
        val spokenWords = lastSpoken.split(' ').toSet()
        val overlap = heardWords.count { it in spokenWords }.toDouble() / heardWords.size
        return overlap >= 0.6
    }

    /**
     * Человек договорил ещё до того, как включился хороший распознаватель.
     * Тогда уходит то, что расслышала локальная модель, — хуже, но не тишина.
     */
    private fun deliverFallback() {
        val text = fallbackText
        fallbackText = ""
        if (text.isBlank()) return
        report("расслышано локально: $text")
        deliver(text)
    }

    private fun deliver(payload: String, alternatives: List<String> = emptyList()) {
        if (payload.isBlank()) return
        report("→ $payload")
        send(
            JSONObject()
                .put("id", "speech_segment")
                .put("segment_id", System.currentTimeMillis().toString())
                .put("transcript", payload)
                // Демон не может услышать, по какому каналу пришёл звук: на
                // канале гарнитуры полоса узкая и часть признаков не измерить.
                .put("device", if (route?.onBluetoothMic == true) "sony_mic" else "phone_mic")
                .put("narrowband", route?.onBluetoothMic == true)
                .put("duration_ms", 1200)
                .put("voiced_frames", 40)
                .put("role", "master")
                .apply { if (alternatives.isNotEmpty()) put("alternatives", JSONArray(alternatives)) }
        )
    }

    /** Реплика на выбранном языке распознавателем телефона. */
    /**
     * Реплика уже прозвучала целиком, и хороший распознаватель ждёт речь,
     * которой не будет. Ждать его собственный таймаут — это секунды тишины
     * в ухе, поэтому обрываем сами и отправляем то, что расслышали локально.
     */
    private val fallbackTimer = Runnable {
        if (awaitingCommand && fallbackText.isNotBlank()) {
            runCatching { cloud?.cancel() }
            finishCommand()
            deliverFallback()
        }
    }

    private fun listenForCommand(fallback: String = "") {
        if (awaitingCommand) return
        awaitingCommand = true
        fallbackText = fallback
        main.post {
            runCatching { wake?.stop() }
            try {
                if (cloud == null) cloud = SpeechRecognizer.createSpeechRecognizer(this)
                val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE, prefs.language)
                    putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
                    // Несколько гипотез: распознаватель почти всегда держит
                    // верный вариант вторым, когда путает имя из проекта.
                    putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5)
                    putExtra("android.speech.extra.ENABLE_LANGUAGE_SWITCH", "adaptive")
                    putStringArrayListExtra(
                        "android.speech.extra.LANGUAGE_SWITCH_ALLOWED_LANGUAGES",
                        arrayListOf("ru-RU", "en-US", "he-IL")
                    )
                }
                cloud?.setRecognitionListener(commandListener())
                cloud?.startListening(intent)
                if (fallback.isNotBlank()) main.postDelayed(fallbackTimer, FALLBACK_MS)
            } catch (t: Throwable) {
                awaitingCommand = false
                report("распознавание недоступно: ${t.javaClass.simpleName}")
                deliverFallback()
                resumeWake()
            }
        }
    }

    private fun commandListener() = object : CloudListener {
        override fun onResults(results: Bundle?) {
            val heard = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                .orEmpty().map { it.trim() }.filter { it.isNotEmpty() }
            val text = heard.firstOrNull().orEmpty()
            finishCommand()
            if (text.isEmpty()) {
                deliverFallback()
                return
            }
            fallbackText = ""
            Intents.stopIntent(text)?.let { scope ->
                silence()
                send(JSONObject().put("id", "interrupt").put("scope", scope))
                return
            }
            Intents.languageSwitch(text)?.let { code ->
                prefs.language = code
                report("язык: $code")
                return
            }
            deliver(text, heard.drop(1).take(3))
        }

        override fun onError(error: Int) {
            finishCommand()
            if (error != SpeechRecognizer.ERROR_NO_MATCH &&
                error != SpeechRecognizer.ERROR_SPEECH_TIMEOUT
            ) {
                report("распознавание реплики: ошибка $error")
            }
            deliverFallback()
        }

        override fun onReadyForSpeech(params: Bundle?) { report("слушаю реплику…") }

        /** Человек всё-таки говорит — запасной вариант больше не нужен. */
        override fun onBeginningOfSpeech() {
            main.removeCallbacks(fallbackTimer)
            fallbackText = ""
        }
        override fun onRmsChanged(rmsdB: Float) = Unit
        override fun onBufferReceived(buffer: ByteArray?) = Unit
        override fun onEndOfSpeech() = Unit
        override fun onPartialResults(partialResults: Bundle?) = Unit
        override fun onEvent(eventType: Int, params: Bundle?) = Unit
    }

    private fun finishCommand() {
        awaitingCommand = false
        main.removeCallbacks(fallbackTimer)
        main.postDelayed({ if (!awaitingCommand) resumeWake() }, 300)
    }

    private fun resumeWake() {
        if (!wakeReady) return
        runCatching {
            wake?.start(
                onText = { text -> main.post { handle(text) } },
                onError = { t -> report("распознавание обращения: ${t.message}") }
            )
        }.onFailure { report("wake word остановился: ${it.javaClass.simpleName}") }
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

    /** Кнопка разрешает реплику без обращения, а без wake word — начинает её. */
    private fun armWindow() {
        if (speaking) silence()
        windowUntil = System.currentTimeMillis() + WINDOW_MS
        report("слушаю — говори")
        if (!wakeReady || prefs.language != "ru-RU") listenForCommand()
    }

    // ---------- речь ----------
    /**
     * Мужской голос выбираем по имени, а если таких нет — берём самый
     * качественный из доступных для языка. Ниже стандартного робота в любом
     * случае не будет.
     */
    private fun pickVoice(engine: TextToSpeech, languageTag: String): Voice? {
        val wanted = Locale.forLanguageTag(languageTag).language
        val voices = runCatching { engine.voices.orEmpty() }.getOrDefault(emptySet())
            .filter { it.locale.language == wanted && !it.isNetworkConnectionRequired }
        if (voices.isEmpty()) return null
        prefs.voice.takeIf { it.isNotBlank() }
            ?.let { saved -> voices.firstOrNull { it.name == saved } }
            ?.let { return it }
        val male = voices.filter {
            val name = it.name.lowercase()
            ("male" in name && "female" !in name) || name.endsWith("-rud-local") ||
                name.endsWith("-ruc-local") || "#male" in name
        }
        return (male.ifEmpty { voices }).maxByOrNull { it.quality }
    }

    private fun applyVoice(languageTag: String) {
        val engine = tts ?: return
        runCatching { engine.language = Locale.forLanguageTag(languageTag) }
        runCatching { pickVoice(engine, languageTag)?.let { engine.voice = it } }
    }

    private fun setUpTts() {
        val engine = prefs.engine.takeIf { it.isNotBlank() }
        val listener = TextToSpeech.OnInitListener { code ->
            if (code == TextToSpeech.SUCCESS) {
                applyVoice(prefs.language)
                runCatching { tts?.setSpeechRate(1.02f) }
                tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String?) { speaking = true }
                    override fun onDone(utteranceId: String?) {
                        speaking = false
                        spokeAt = System.currentTimeMillis()
                        windowUntil = System.currentTimeMillis() + WINDOW_MS
                        // Хвост фразы ещё звучит в комнате — ждём, потом слушаем.
                        main.postDelayed({ if (!speaking && !awaitingCommand) resumeWake() }, 600)
                    }
                    @Deprecated("deprecated in API 21")
                    override fun onError(utteranceId: String?) { speaking = false }
                })
            } else {
                report("синтез речи не запустился (код $code)")
            }
        }
        tts = if (engine != null) TextToSpeech(this, listener, engine)
              else TextToSpeech(this, listener)
    }

    /** Играет ли звук в наушники — тогда микрофон его не услышит. */
    private fun onHeadphones(): Boolean = route?.onBluetoothMic == true || runCatching {
        getSystemService(AudioManager::class.java)
            .getDevices(AudioManager.GET_DEVICES_OUTPUTS)
            .any {
                it.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP ||
                    it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO ||
                    it.type == AudioDeviceInfo.TYPE_WIRED_HEADPHONES ||
                    it.type == AudioDeviceInfo.TYPE_WIRED_HEADSET ||
                    it.type == AudioDeviceInfo.TYPE_USB_HEADSET
            }
    }.getOrDefault(false)

    /**
     * Канал гарнитуры — это канал разговора. Если говорить в музыкальный поток,
     * пока он поднят, голос уедет в динамик телефона: и эхо, и разбуженные
     * соседи. Поэтому поток выбираем по тому, где сейчас микрофон.
     */
    private fun speechParams(): Bundle = Bundle().apply {
        putInt(
            TextToSpeech.Engine.KEY_PARAM_STREAM,
            if (route?.onBluetoothMic == true) AudioManager.STREAM_VOICE_CALL
            else AudioManager.STREAM_MUSIC
        )
    }

    private fun speak(text: String) {
        if (text.isBlank() || prefs.mute) return
        // Эхо сравниваем по чистому тексту: распознаватель знаков не вернёт.
        lastSpoken = Intents.normalise(Stress.strip(text))
        spokeAt = System.currentTimeMillis()
        // Через динамик микрофон слышит нас самих: на это время он засыпает.
        // В наушниках эхо-пути нет, и «стоп» продолжает работать.
        if (!onHeadphones()) runCatching { wake?.stop() }
        runCatching {
            applyVoice(Intents.scriptLanguage(text, prefs.language))
            tts?.speak(Stress.render(text, prefs.stressStyle, prefs.engine),
                       TextToSpeech.QUEUE_FLUSH, speechParams(), "voice-shell")
        }
    }

    private fun silence() {
        runCatching { tts?.stop() }
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
                unauthorized = false
                webSocket.send(
                    JSONObject().put("id", "hello").put("v", 1)
                        .put("token", prefs.token).put("device_id", "android")
                        .put("app_version", "0.4.0").toString()
                )
                report("подключено")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                main.post { runCatching { onServerMessage(JSONObject(text)) } }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                val hint = when {
                    t.message?.contains("CLEARTEXT") == true ->
                        "нет связи: сервер по http запрещён системой — обнови приложение"
                    t.message?.contains("Failed to connect") == true ->
                        "нет связи: сервер недоступен — проверь адрес, порт и фаервол"
                    else -> "нет связи: ${t.message}"
                }
                report(hint)
                main.postDelayed({ connect() }, 4000)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                // С неверным токеном долбиться раз в две секунды бессмысленно.
                main.postDelayed({ connect() }, if (unauthorized) 30_000 else 2000)
            }
        })
    }

    private fun onServerMessage(message: JSONObject) {
        when (message.optString("id")) {
            "welcome" -> report("готов · ${message.optString("credential")}")
            "voice_summary" -> {
                val text = message.optString("text")
                // На экран — чистый текст, в синтез — с ударениями, если они есть.
                val spoken = message.optString("spoken").ifBlank { text }
                report(text)
                speak(spoken)
            }
            "permission_request" -> {
                val spoken = message.optString("spoken")
                report(spoken)
                speak(spoken)
            }
            "auth_url" -> {
                val url = message.optString("url")
                report("открой ссылку, войди в Claude и вставь код")
                sendBroadcast(
                    Intent(ACTION_STATUS).setPackage(packageName)
                        .putExtra(EXTRA_TEXT, "ссылка авторизации получена")
                        .putExtra(EXTRA_AUTH_URL, url)
                )
            }
            "auth_token" -> {
                val persisted = message.optBoolean("persisted")
                report(
                    if (persisted) "подписка подключена и сохранена на сервере"
                    else "подписка подключена, но не сохранилась — впиши токен в файл службы"
                )
                sendBroadcast(
                    Intent(ACTION_STATUS).setPackage(packageName)
                        .putExtra(EXTRA_TEXT, if (persisted) "подписка подключена" else "подписка подключена (не сохранена)")
                        .putExtra(EXTRA_AUTH_TOKEN, message.optString("token"))
                )
            }
            "auth_error" -> report("авторизация не вышла: ${message.optString("message")}")
            "whisper" -> speak(message.optString("text"))
            "error" -> {
                val code = message.optString("code")
                if (code == "unauthorized") {
                    unauthorized = true
                    report("токен не подошёл — вставь только значение, без VOICE_TOKEN=")
                } else {
                    report("ошибка: ${message.optString("message")}")
                }
            }
        }
    }

    private fun send(payload: JSONObject) {
        val ws = socket
        if (ws == null) { report("нет связи"); return }
        ws.send(payload.toString())
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
        val listen = PendingIntent.getService(
            this, 2, Intent(this, VoiceService::class.java).setAction(ACTION_LISTEN),
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
            .addAction(0, "Говорить", listen)
            .addAction(0, "Стоп", stop)
            .setOngoing(true)
            .setSilent(true)
            .build()
    }

    private fun report(text: String) {
        Log.i(TAG, text)
        main.post {
            runCatching {
                getSystemService(NotificationManager::class.java)
                    .notify(NOTIFICATION_ID, notification(text))
            }
            sendBroadcast(Intent(ACTION_STATUS).setPackage(packageName).putExtra(EXTRA_TEXT, text))
        }
    }

    private fun fail(reason: String) {
        Log.e(TAG, reason)
        runCatching { prefs.lastError = reason }
        sendBroadcast(Intent(ACTION_STATUS).setPackage(packageName).putExtra(EXTRA_TEXT, reason))
        stopSelf()
    }
}
