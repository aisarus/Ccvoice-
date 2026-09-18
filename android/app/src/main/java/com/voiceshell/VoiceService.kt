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
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.media.MediaPlayer
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
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
import android.view.KeyEvent
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.media.session.MediaButtonReceiver
import androidx.core.content.ContextCompat
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
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
        const val ACTION_ACOUSTICS = "com.voiceshell.ACOUSTICS"
        const val ACTION_RECONNECT = "com.voiceshell.RECONNECT"
        const val EXTRA_VOICES = "voices"
        const val EXTRA_TEXT = "text"
        /** Состояние связи отдельным полем: экрану не надо гадать по строке. */
        const val EXTRA_LINK = "link"
        const val EXTRA_CODE = "code"
        const val EXTRA_AUTH_URL = "auth_url"
        const val EXTRA_AUTH_TOKEN = "auth_token"
        private const val CHANNEL = "voice-shell"
        private const val NOTIFICATION_ID = 42
        private const val WINDOW_MS = 15_000L
        /** Столько ждём, что человек начнёт говорить, прежде чем взять расслышанное. */
        private const val FALLBACK_MS = 3_500L
        /** Распознаватель обязан ответить хоть чем-то; молчит дольше — он мёртв. */
        private const val RECOGNIZER_DEADLINE_MS = 15_000L
        /** Сколько ждать конца произнесения, если движок забыл сказать «готово». */
        private const val SPEECH_MARGIN_MS = 5_000L
        private const val SPEECH_PER_CHAR_MS = 80L
        private const val SPEECH_CAP_MS = 120_000L
        /** Раз в полминуты проверяем, что нас всё ещё можно позвать. */
        private const val HEARTBEAT_MS = 30_000L
        private const val TAG = "VoiceShell"
    }

    private lateinit var prefs: Prefs
    private val main = Handler(Looper.getMainLooper())
    private val http = OkHttpClient.Builder().pingInterval(20, TimeUnit.SECONDS).build()

    private var socket: WebSocket? = null
    private var tts: TextToSpeech? = null
    // Запасной движок — системный. Нужен ровно тогда, когда выбранный не
    // умеет язык ответа: RHVoice прекрасно говорит по-русски и не знает
    // иврита вовсе, и реплика просто не звучала.
    private var spare: TextToSpeech? = null
    private var spareReady = false
    private var pending: Pair<String, String>? = null
    // О каждом недостающем голосе говорим один раз, а не на каждой реплике.
    private val voicelessSaid = mutableSetOf<String>()
    private var session: MediaSessionCompat? = null
    private var cloud: SpeechRecognizer? = null
    private var wake: WakeWordEngine? = null
    private var route: AudioRoute? = null
    private var signals: Signals? = null
    /** Громкость реплики: единственное, что телефон может измерить сам. */
    private val meter = SpeechMeter()
    /** Что происходит прямо сейчас — первая строка уведомления. */
    private var phase = R.string.state_waiting_for_wake

    private var speaking = false
    private var awaitingCommand = false
    private var lastSpoken = ""
    private var lastLine = ""
    private var spokeAt = 0L
    private var windowUntil = 0L
    private var wakeReady = false
    private var fallbackText = ""
    private var unauthorized = false

    /** Сколько реплик подряд демон отверг как чужую речь. */
    private var roleGates = 0

    private var linkState = LinkState.OFF
    private var attempt = 0
    private var reconnectScheduled = false
    /** Служба уже остановлена: отложенные попытки связи должны умереть вместе с ней. */
    @Volatile private var stopped = false

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        lastLine = getString(R.string.state_starting)

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            fail(getString(R.string.error_mic_permission))
            return
        }

        try {
            createChannel()
            ServiceCompat.startForeground(
                this, NOTIFICATION_ID, notification(lastLine),
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
            )
        } catch (t: Throwable) {
            fail(getString(R.string.error_service_start, "${t.javaClass.simpleName}: ${t.message}"))
            return
        }

        try {
            setUpTts()
            setUpMediaSession()
            connect()
            prefs.lastError = ""
        } catch (t: Throwable) {
            fail(getString(R.string.error_startup, "${t.javaClass.simpleName}: ${t.message}"))
            return
        }

        // Маршрут поднимаем до wake word: поток записи не переезжает на другое
        // устройство сам, и открывать его надо уже на нужном микрофоне.
        signals = Signals(this)
        route = AudioRoute(
            this,
            onChanged = { why -> main.post { onRouteChanged(why) } },
            log = { line -> report(line) }
        )
        runCatching { route?.start(prefs.btMic) }
        report(route?.describe().orEmpty().ifBlank { getString(R.string.mic_phone) })

        // Сеть вернулась — незачем досиживать паузу до конца: человек уже
        // говорит в телефон и ждёт ответа.
        runCatching {
            getSystemService(ConnectivityManager::class.java)
                .registerDefaultNetworkCallback(networkWatch)
        }

        Thread { prepareWakeWord() }.start()
        main.postDelayed(heartbeat, HEARTBEAT_MS)
    }

    private val networkWatch = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            main.post { if (socket == null && linkState != LinkState.OFF) connectNow() }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            // Нажатие на гарнитуре приходит сюда через androidx-приёмник.
            Intent.ACTION_MEDIA_BUTTON -> MediaButtonReceiver.handleIntent(session, intent)
            ACTION_STOP -> { stopSelf(); return START_NOT_STICKY }
            ACTION_LISTEN -> armWindow()
            ACTION_MIC -> {
                route?.enable(prefs.btMic)
                report(route?.describe().orEmpty().ifBlank { getString(R.string.mic_phone) })
            }
            ACTION_ACOUSTICS -> report(
                getString(
                    if (prefs.acoustics) R.string.acoustics_measuring
                    else R.string.acoustics_hint_only
                )
            )
            // Человек нажал «повторить» на экране готовности: ждать паузу незачем.
            ACTION_RECONNECT -> connectNow()
            ACTION_SAY -> {
                val text = intent.getStringExtra(EXTRA_TEXT).orEmpty().trim()
                if (text.isNotEmpty()) deliver(text)
            }
            ACTION_ENGINES -> {
                val engines = runCatching { tts?.engines.orEmpty() }.getOrDefault(emptyList())
                sendBroadcast(
                    Intent(ACTION_STATUS).setPackage(packageName)
                        .putExtra(EXTRA_TEXT, getString(R.string.engines_found, engines.size))
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
                // Запасной движок системный, и от смены выбранного не
                // меняется, — но о недостающих голосах стоит сказать заново.
                voicelessSaid.clear()
                setUpTts()
                val named = engine.ifBlank { getString(R.string.engine_system) }
                report(getString(R.string.engine_set, named))
            }
            ACTION_VOICES -> {
                val names = runCatching { tts?.voices.orEmpty() }.getOrDefault(emptySet())
                    .filter { it.locale.language == Locale.forLanguageTag(prefs.language).language }
                    .sortedByDescending { it.quality }
                    .map { it.name }
                sendBroadcast(
                    Intent(ACTION_STATUS).setPackage(packageName)
                        .putExtra(EXTRA_TEXT, getString(R.string.voices_found, names.size))
                        .putStringArrayListExtra(EXTRA_VOICES, ArrayList(names))
                )
            }
            ACTION_TRY_VOICE -> {
                val name = intent.getStringExtra(EXTRA_CODE).orEmpty()
                if (name.isNotBlank()) {
                    prefs.voice = name
                    // Образец звучит на языке ответа: голос выбирают, чтобы
                    // послушать его, а не чтобы проверить распознавание.
                    val tag = prefs.replyLanguage.ifBlank { prefs.language }
                    tts?.let { applyVoice(it, tag) }
                    val sample = mapOf(
                        "ru-RU" to "Готово. Все сорок семь тестов проходят.",
                        "en-US" to "Done. All forty seven tests pass.",
                        "es-ES" to "Listo. Las cuarenta y siete pruebas pasan.",
                        "zh-CN" to "好了。四十七个测试全部通过。",
                        "he-IL" to "מוכן. כל הבדיקות עוברות."
                    )[tag].orEmpty()
                    tts?.speak(sample, TextToSpeech.QUEUE_FLUSH, speechParams(), "voice-shell-sample")
                }
            }
            ACTION_AUTH_SET -> {
                val token = intent.getStringExtra(EXTRA_CODE).orEmpty().trim()
                if (token.isNotEmpty()) {
                    report(getString(R.string.auth_checking_token))
                    send(JSONObject().put("id", "auth_set").put("token", token))
                }
            }
            ACTION_AUTH_START -> {
                report(getString(R.string.auth_requesting_url))
                send(JSONObject().put("id", "auth_start"))
            }
            ACTION_AUTH_CODE -> {
                val code = intent.getStringExtra(EXTRA_CODE).orEmpty().trim()
                if (code.isNotEmpty()) {
                    report(getString(R.string.auth_checking_code))
                    send(JSONObject().put("id", "auth_code").put("code", code))
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        stopped = true
        main.removeCallbacksAndMessages(null)
        runCatching {
            getSystemService(ConnectivityManager::class.java)
                .unregisterNetworkCallback(networkWatch)
        }
        runCatching { signals?.release() }
        runCatching { route?.release() }
        runCatching { wake?.release() }
        runCatching { cloud?.destroy() }
        runCatching { tts?.shutdown() }
        runCatching { spare?.shutdown() }
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
                onError = { t -> report(getString(R.string.wake_error, t.message.orEmpty())) }
            )
            wake = engine
            wakeReady = true
            report(getString(R.string.state_listening_for_wake))
        } catch (t: Throwable) {
            // Самый частый случай: не поднялась нативная библиотека.
            Log.e(TAG, "wake word", t)
            wakeReady = false
            report(getString(R.string.wake_unavailable, t.javaClass.simpleName))
        }
    }

    /**
     * Маршрут микрофона сменился: гарнитуру надели, сняли или канал переоткрыли.
     * Уже открытый поток записи остался на старом устройстве — перезапускаем.
     */
    private fun onRouteChanged(why: String) {
        Log.i(TAG, "route: $why")
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
            report(getString(if (scope == "work") R.string.stopping_work else R.string.going_quiet))
            return
        }
        Intents.listenSwitch(text)?.let { change ->
            switchListening(change)
            return
        }
        Intents.languageSwitch(text)?.let { code ->
            switchReplyLanguage(code, aloud = true)
            return
        }
        if (speaking) {
            // Обращение посреди ответа — это перебивание. Раньше оно молча
            // отбрасывалось, и выглядело это как «отзывается через раз».
            if (!Intents.hasWake(text)) return
            silence()
        }
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
        report(getString(R.string.heard_locally, text))
        deliver(text)
    }

    private fun deliver(
        payload: String,
        alternatives: List<String> = emptyList(),
        measured: Segment? = null,
    ) {
        if (payload.isBlank()) return
        signals?.accepted(route?.onBluetoothMic == true)
        phase(R.string.state_sent)
        report(getString(R.string.sent_to, payload))
        val device = if (route?.onBluetoothMic == true) "sony_mic" else "phone_mic"
        send(
            JSONObject()
                .put("id", "speech_segment")
                .put("segment_id", System.currentTimeMillis().toString())
                .put("transcript", payload)
                // Демон не может услышать, по какому каналу пришёл звук: на
                // канале гарнитуры полоса узкая и часть признаков не измерить.
                .put("device", device)
                .put("narrowband", route?.onBluetoothMic == true)
                // Роль — не измерение, а предположение устройства: телефон в
                // кармане у хозяина. Демон, у которого есть признаки, её и не
                // спрашивает, но без признаков он должен знать, чего она стоит.
                .put("role", "master")
                .put("role_source", "hint")
                .apply { if (alternatives.isNotEmpty()) put("alternatives", JSONArray(alternatives)) }
                .apply { describe(this, device, measured) }
        )
    }

    /**
     * Кладёт в сообщение то, что удалось измерить, — и ничего сверх этого.
     *
     * Длительность и число речевых кадров демон использует в жёстких гейтах, а
     * признаки — в классификаторе. Поэтому непомеренное не подставляется
     * умолчаниями: пусть демон применит свои, чем мы соврём про сегмент,
     * которого не слышали (реплика по кнопке или текстом).
     */
    private fun describe(message: JSONObject, device: String, measured: Segment?) {
        // Выключатель возвращает поведение целиком: ни признаков, ни
        // измеренной длительности — демон применит свои умолчания, как раньше.
        if (measured == null || !prefs.acoustics) return
        message.put("duration_ms", measured.durationMs)
        message.put("voiced_frames", Acoustics.voicedFrames(measured))
        val baseline = prefs.baseline(device)
        val features = Acoustics.features(measured, baseline)
        if (features.isEmpty()) {
            // Первая реплика на этом микрофоне задаёт норму: сравнивать пока
            // не с чем, и выдумывать «ноль» нельзя — это сказало бы демону,
            // что говорили ровно как обычно.
            report(getString(R.string.calibrating_mic))
        } else {
            val json = JSONObject()
            for ((name, value) in features) json.put(name, value)
            message.put("features", json)
        }
        prefs.setBaseline(device, Acoustics.nextBaseline(measured, baseline))
    }

    /** Реплика на выбранном языке распознавателем телефона. */
    /**
     * Реплика уже прозвучала целиком, и хороший распознаватель ждёт речь,
     * которой не будет. Ждать его собственный таймаут — это секунды тишины
     * в ухе, поэтому обрываем сами и отправляем то, что расслышали локально.
     */
    /**
     * Сторож распознавателя.
     *
     * Он может не ответить ни результатом, ни ошибкой — тогда awaitingCommand
     * остаётся поднятым навсегда: wake word остановлен, новые реплики не
     * принимаются. Первая проходит, вторая — уже нет.
     */
    private val recognizerWatchdog = Runnable {
        if (awaitingCommand) {
            report(getString(R.string.recognizer_silent))
            runCatching { cloud?.cancel() }
            finishCommand()
            deliverFallback()
        }
    }

    /**
     * Сторож синтеза: если движок не скажет «готово», speaking останется
     * поднятым, и каждая следующая реплика отбросится на первой же строке.
     */
    private val speechWatchdog = Runnable {
        if (speaking) {
            speaking = false
            spokeAt = System.currentTimeMillis()
            windowUntil = System.currentTimeMillis() + WINDOW_MS
            report(getString(R.string.tts_silent))
            if (!awaitingCommand) resumeWake()
        }
    }

    /**
     * Последняя линия обороны: раз в полминуты проверяем, что нас можно
     * позвать. Любая причина, по которой слушатель встал, лечится одинаково.
     */
    private val heartbeat = object : Runnable {
        override fun run() {
            if (wakeReady && !speaking && !awaitingCommand && wake?.isRunning != true) {
                report(getString(R.string.listener_restarted))
                resumeWake()
            }
            main.postDelayed(this, HEARTBEAT_MS)
        }
    }

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
            // Копилка громкости — только про эту реплику: кадры прошлой
            // сделали бы «фоном» чужой голос из прошлого разговора.
            meter.reset()
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
                    // Распознавание нескольких языков сразу (Android 13+).
                    //
                    // Здесь уже стояла попытка это включить, и она не
                    // работала: значение «adaptive» такого API не бывает —
                    // валидные это high_precision, balanced, quick_response, —
                    // а переключение языков без включённого их определения не
                    // работает вовсе. Отсюда и «на иврите не слышит».
                    //
                    // Даже так это «по возможности»: extras исполняет служба
                    // распознавания, и не всякая их поддерживает. Не
                    // поддержала — молча слушает один язык, как раньше.
                    if (prefs.multilingual && Build.VERSION.SDK_INT >= 33) {
                        val allowed = ArrayList(
                            listOf(prefs.language) + Prefs.SUPPORTED.filter { it != prefs.language }
                        )
                        putExtra(RecognizerIntent.EXTRA_ENABLE_LANGUAGE_DETECTION, true)
                        putStringArrayListExtra(
                            RecognizerIntent.EXTRA_LANGUAGE_DETECTION_ALLOWED_LANGUAGES, allowed)
                        putExtra(RecognizerIntent.EXTRA_ENABLE_LANGUAGE_SWITCH,
                                 RecognizerIntent.LANGUAGE_SWITCH_BALANCED)
                        putStringArrayListExtra(
                            RecognizerIntent.EXTRA_LANGUAGE_SWITCH_ALLOWED_LANGUAGES, allowed)
                    }
                }
                cloud?.setRecognitionListener(commandListener())
                cloud?.startListening(intent)
                if (fallback.isNotBlank()) main.postDelayed(fallbackTimer, FALLBACK_MS)
                main.postDelayed(recognizerWatchdog, RECOGNIZER_DEADLINE_MS)
            } catch (t: Throwable) {
                awaitingCommand = false
                report(getString(R.string.recognition_unavailable, t.javaClass.simpleName))
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
                if (fallbackText.isBlank()) {
                    signals?.missed(route?.onBluetoothMic == true)
                    phase(R.string.state_waiting_for_wake)
                }
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
                switchReplyLanguage(code, aloud = false)
                return
            }
            deliver(text, heard.drop(1).take(3), meter.segment())
        }

        override fun onError(error: Int) {
            finishCommand()
            if (error != SpeechRecognizer.ERROR_NO_MATCH &&
                error != SpeechRecognizer.ERROR_SPEECH_TIMEOUT
            ) {
                report(getString(R.string.recognition_error, error))
            }
            if (fallbackText.isBlank()) {
                signals?.missed(route?.onBluetoothMic == true)
                phase(R.string.state_waiting_for_wake)
            }
            deliverFallback()
        }

        override fun onReadyForSpeech(params: Bundle?) {
            signals?.listening(route?.onBluetoothMic == true)
            phase(R.string.state_listening)
            report(getString(R.string.listening_for_reply))
        }

        /** Человек всё-таки говорит — запасной вариант больше не нужен. */
        override fun onBeginningOfSpeech() {
            main.removeCallbacks(fallbackTimer)
            fallbackText = ""
            meter.speechBegan(System.currentTimeMillis())
        }

        /**
         * Единственное окно в сам звук: доступа к записи у нас нет, а эта
         * череда значений и есть то, из чего считаются признаки говорящего.
         */
        override fun onRmsChanged(rmsdB: Float) {
            meter.frame(rmsdB, System.currentTimeMillis())
        }

        override fun onBufferReceived(buffer: ByteArray?) = Unit

        override fun onEndOfSpeech() {
            meter.speechEnded(System.currentTimeMillis())
        }
        override fun onPartialResults(partialResults: Bundle?) = Unit
        override fun onEvent(eventType: Int, params: Bundle?) = Unit
    }

    private fun finishCommand() {
        awaitingCommand = false
        main.removeCallbacks(fallbackTimer)
        main.removeCallbacks(recognizerWatchdog)
        main.postDelayed({ if (!awaitingCommand) resumeWake() }, 300)
    }

    private fun resumeWake() {
        if (!wakeReady) return
        runCatching {
            wake?.start(
                onText = { text -> main.post { handle(text) } },
                onError = { t -> report(getString(R.string.wake_error, t.message.orEmpty())) }
            )
        }.onFailure { report(getString(R.string.wake_stopped, it.javaClass.simpleName)) }
    }

    // ---------- кнопка гарнитуры ----------
    private fun setUpMediaSession() {
        session = MediaSessionCompat(this, "VoiceShell").apply {
            setPlaybackState(
                PlaybackStateCompat.Builder()
                    // Объявлять надо ровно то, что обрабатываешь: система
                    // отбрасывает необъявленное действие до колбэка, и
                    // двойное касание — то есть «следующий трек» — не
                    // доходило никогда, хотя обработчик для него стоял.
                    .setActions(
                        PlaybackStateCompat.ACTION_PLAY or PlaybackStateCompat.ACTION_PAUSE or
                            PlaybackStateCompat.ACTION_PLAY_PAUSE or
                            PlaybackStateCompat.ACTION_STOP or
                            PlaybackStateCompat.ACTION_SKIP_TO_NEXT or
                            PlaybackStateCompat.ACTION_SKIP_TO_PREVIOUS
                    )
                    .setState(PlaybackStateCompat.STATE_PLAYING, 0, 1.0f)
                    .build()
            )
            setCallback(object : MediaSessionCompat.Callback() {
                /**
                 * Видимый след нажатия.
                 *
                 * Кнопка молчит по четырём разным причинам, и снаружи они
                 * неотличимы: гарнитура не шлёт, система отдала другому,
                 * действие не объявлено, обработчик не сработал. Эта строчка
                 * отделяет «не дошло» от «дошло и ничего не сделало».
                 */
                override fun onMediaButtonEvent(intent: Intent): Boolean {
                    val event = runCatching {
                        @Suppress("DEPRECATION")
                        intent.getParcelableExtra<KeyEvent>(Intent.EXTRA_KEY_EVENT)
                    }.getOrNull()
                    val handled = super.onMediaButtonEvent(intent)
                    if (event != null && event.action == KeyEvent.ACTION_DOWN) {
                        val name = KeyEvent.keyCodeToString(event.keyCode)
                        Log.i(TAG, "headset key $name handled=$handled")
                        // Нажатие дошло, но действие незнакомое: это третья,
                        // самая неочевидная поломка, и молчать о ней нельзя —
                        // снаружи она неотличима от «гарнитура ничего не шлёт».
                        if (!handled) report(getString(R.string.headset_key_ignored, name))
                    }
                    return handled
                }
                override fun onPlay() = armWindow()
                override fun onPause() = armWindow()
                override fun onStop() = armWindow()
                override fun onSkipToNext() = armWindow()
                override fun onSkipToPrevious() = armWindow()
            })
            isActive = true
        }
        claimMediaButtons()
    }

    /**
     * Занять очередь на кнопку, проиграв секунду тишины.
     *
     * Android отдаёт нажатие той сессии, которая последней **звучала**, а не
     * той, которая объявила себя играющей. Оболочка, ни разу ничего не
     * сказавшая, в этом споре не участвует вовсе — и кнопка уходит мимо.
     *
     * Держать поток постоянно было бы дороже, чем нужно: канал bluetooth
     * остаётся открытым, и садятся обе батареи. Хватает одного раза при
     * старте: дальше очередь обновляет сама речь оболочки — это настоящий
     * звук, и каждый ответ заново делает нас последними.
     */
    private fun claimMediaButtons() {
        runCatching {
            val file = File(cacheDir, "silence.wav")
            if (!file.exists()) file.writeBytes(silentWav(seconds = 1.0))
            MediaPlayer().apply {
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build()
                )
                setDataSource(file.absolutePath)
                // Не ноль: нулевую громкость система вправе счесть за молчание.
                setVolume(0.0001f, 0.0001f)
                setOnCompletionListener { it.release() }
                setOnErrorListener { player, _, _ -> player.release(); true }
                prepare()
                start()
            }
        }.onFailure { Log.i(TAG, "media button claim: ${it.javaClass.simpleName}") }
    }

    /** WAV из одних нулей: файла в репозитории ради этого заводить незачем. */
    private fun silentWav(seconds: Double): ByteArray {
        val rate = 8000
        val samples = (rate * seconds).toInt()
        val data = samples * 2
        val out = java.io.ByteArrayOutputStream(44 + data)
        fun ascii(s: String) = out.write(s.toByteArray(Charsets.US_ASCII))
        fun int32(v: Int) = out.write(byteArrayOf(
            v.toByte(), (v shr 8).toByte(), (v shr 16).toByte(), (v shr 24).toByte()))
        fun int16(v: Int) = out.write(byteArrayOf(v.toByte(), (v shr 8).toByte()))
        ascii("RIFF"); int32(36 + data); ascii("WAVE")
        ascii("fmt "); int32(16); int16(1); int16(1)
        int32(rate); int32(rate * 2); int16(2); int16(16)
        ascii("data"); int32(data)
        out.write(ByteArray(data))
        return out.toByteArray()
    }

    /**
     * Нажатие на гарнитуре: слушаю прямо сейчас, говори.
     *
     * Раньше кнопка на русском только **взводила** окно, а распознавание
     * начиналось, когда локальная модель услышит речь. Разница незаметна в
     * коде и очень заметна в ухе: нажал — и не понял, услышали тебя или нет.
     * Условие вдобавок спрашивало «русский ли язык», хотя настоящий вопрос —
     * «покроет ли эту речь локальная модель»; языков стало пять, и вопрос
     * стал звучать неправильно раньше, чем начал давать неправильный ответ.
     */
    private fun armWindow() {
        if (speaking) silence()
        windowUntil = System.currentTimeMillis() + WINDOW_MS
        // Сигнал в ухо: с телефоном в кармане это единственное подтверждение,
        // что нажатие дошло.
        signals?.listening(route?.onBluetoothMic == true)
        report(getString(R.string.listening_go_ahead))
        listenForCommand()
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

    /**
     * Возвращает ответ движка, а не выбрасывает его.
     *
     * `setLanguage` на незнакомом языке не бросает исключение и ничего не
     * меняет: он возвращает LANG_NOT_SUPPORTED, а `speak` после этого молчит.
     * Реплики на иврите приходили и не звучали ровно поэтому — ответ движка
     * никто не смотрел.
     */
    private fun applyVoice(engine: TextToSpeech, languageTag: String): Int {
        val status = runCatching { engine.setLanguage(Locale.forLanguageTag(languageTag)) }
            .getOrDefault(TextToSpeech.LANG_NOT_SUPPORTED)
        if (status >= TextToSpeech.LANG_AVAILABLE) {
            runCatching { pickVoice(engine, languageTag)?.let { engine.voice = it } }
        }
        return status
    }

    private fun speaks(engine: TextToSpeech?, languageTag: String): Boolean =
        engine != null && applyVoice(engine, languageTag) >= TextToSpeech.LANG_AVAILABLE

    /** Поднять системный движок про запас. Инициализация асинхронная. */
    private fun ensureSpare() {
        if (spare != null) return
        spare = TextToSpeech(this, TextToSpeech.OnInitListener { code ->
            spareReady = code == TextToSpeech.SUCCESS
            if (!spareReady) return@OnInitListener
            runCatching { spare?.setSpeechRate(1.02f) }
            spare?.setOnUtteranceProgressListener(progressListener())
            // Реплика, из-за которой запасной и поднимали, ещё ждёт.
            val waiting = pending
            pending = null
            if (waiting != null) main.post { say(waiting.first, waiting.second) }
        })
    }

    /**
     * Голоса нет ни у выбранного движка, ни у системного.
     *
     * Молча проглотить реплику нельзя: человек в наушниках не отличит «нет
     * голоса» от «оболочка умерла». Говорим об этом один раз на язык и
     * доводим состояние машины до конца, как если бы речь закончилась.
     */
    private fun noVoiceFor(languageTag: String) {
        val name = Locale.forLanguageTag(languageTag)
            .getDisplayLanguage(Locale.forLanguageTag(prefs.language))
        if (voicelessSaid.add(languageTag)) {
            report(getString(R.string.tts_no_voice, name))
            runCatching {
                val engine = tts
                if (speaks(engine, prefs.language)) {
                    engine?.speak(getString(R.string.tts_no_voice_spoken),
                                  TextToSpeech.QUEUE_FLUSH, speechParams(), "voice-shell")
                }
            }
        }
        speaking = false
        spokeAt = System.currentTimeMillis()
        windowUntil = System.currentTimeMillis() + WINDOW_MS
        if (!awaitingCommand) resumeWake()
    }

    /** Один и тот же слушатель на оба движка: состояние машины у них общее. */
    private fun progressListener() = object : UtteranceProgressListener() {
        override fun onStart(utteranceId: String?) {
            speaking = true
            phase(R.string.state_speaking)
        }
        override fun onDone(utteranceId: String?) {
            main.removeCallbacks(speechWatchdog)
            speaking = false
            phase(R.string.state_waiting_for_wake)
            spokeAt = System.currentTimeMillis()
            windowUntil = System.currentTimeMillis() + WINDOW_MS
            // Хвост фразы ещё звучит в комнате — ждём, потом слушаем.
            main.postDelayed({ if (!speaking && !awaitingCommand) resumeWake() }, 600)
        }
        @Deprecated("deprecated in API 21")
        override fun onError(utteranceId: String?) {
            main.removeCallbacks(speechWatchdog)
            speaking = false
            // Молчание после ошибки — это тоже глухота.
            main.postDelayed({ if (!speaking && !awaitingCommand) resumeWake() }, 300)
        }
    }

    private fun setUpTts() {
        val engine = prefs.engine.takeIf { it.isNotBlank() }
        val listener = TextToSpeech.OnInitListener { code ->
            if (code == TextToSpeech.SUCCESS) {
                tts?.let { applyVoice(it, prefs.language) }
                runCatching { tts?.setSpeechRate(1.02f) }
                tts?.setOnUtteranceProgressListener(progressListener())
            } else {
                report(getString(R.string.tts_failed, code))
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

    /**
     * «Клод, английский» меняет язык ответа, а не язык распознавания.
     *
     * Распознаватель Android слушает ровно один язык за раз — это его
     * ограничение, а не выбор человека, и таскать микрофон вслед за ответом
     * значило бы оглохнуть на том языке, на котором только что говорили.
     * Понимать оболочка продолжает всё, что понимала: команды сопоставляются
     * на всех языках сразу, а Claude отвечает на чём угодно.
     */
    private fun switchReplyLanguage(code: String, aloud: Boolean) {
        prefs.replyLanguage = if (code == Intents.ANY_LANGUAGE) "" else code
        voicelessSaid.clear()
        send(JSONObject().put("id", "set_language").put("reply", prefs.replyLanguage))
        val spoken = prefs.replyLanguage.ifBlank { prefs.language }
        report(getString(R.string.language_set, spoken))
        if (aloud) say(Intents.switchNotice(prefs.replyLanguage, prefs.language), spoken)
    }

    /**
     * «Слушай все языки» / «слушай только по-русски».
     *
     * Распознаватель уже начатую реплику не переслушает — новые extras
     * подействуют со следующей, поэтому клиента сбрасываем сразу.
     */
    private fun switchListening(change: Intents.Listen) {
        change.language?.let { prefs.language = it }
        change.multilingual?.let { prefs.multilingual = it }
        runCatching { cloud?.destroy() }
        cloud = null
        val spoken = prefs.replyLanguage.ifBlank { prefs.language }
        val line = when {
            change.language != null -> getString(R.string.listening_language, change.language)
            change.multilingual == true -> getString(R.string.listening_multilingual)
            else -> getString(R.string.listening_one_language)
        }
        report(line)
        say(line, spoken)
    }

    private fun speak(text: String) {
        if (text.isBlank() || prefs.mute) return
        say(text, replyLanguageFor(text))
    }

    /**
     * На каком языке читать этот ответ.
     *
     * Закреплённый голосом язык побеждает: человек просил отвечать на нём, и
     * письменность пришедшего текста этого не отменяет. Без закрепления —
     * по письменности самого ответа, как и было.
     */
    private fun replyLanguageFor(text: String): String =
        prefs.replyLanguage.ifBlank { Intents.scriptLanguage(text, prefs.language) }

    /**
     * Сказать вслух — тем движком, который этот язык умеет.
     *
     * Выбранный движок может языка не знать: RHVoice говорит по-русски и не
     * знает иврита вовсе. Выглядело это как «реплика пришла, но не
     * прозвучала»: `setLanguage` возвращал отказ, `speak` после него молчал,
     * и ответа движка никто не смотрел. Теперь смотрим: не может выбранный —
     * пробуем системный, не может и он — говорим об этом.
     */
    private fun say(text: String, languageTag: String) {
        if (text.isBlank()) return
        // Эхо сравниваем по чистому тексту: распознаватель знаков не вернёт.
        lastSpoken = Intents.normalise(Stress.strip(text))
        spokeAt = System.currentTimeMillis()
        // Через динамик микрофон слышит нас самих: на это время он засыпает.
        // В наушниках эхо-пути нет, и «стоп» продолжает работать.
        if (!onHeadphones()) runCatching { wake?.stop() }

        val engine = when {
            speaks(tts, languageTag) -> tts
            spareReady -> if (speaks(spare, languageTag)) spare else null
            else -> {
                // Системный движок ещё не поднят: поднимаем и договариваем
                // эту же реплику, когда он будет готов.
                pending = text to languageTag
                ensureSpare()
                return
            }
        }
        if (engine == null) {
            noVoiceFor(languageTag)
            return
        }
        runCatching {
            engine.speak(Stress.render(text, prefs.stressStyle, prefs.engine),
                         TextToSpeech.QUEUE_FLUSH, speechParams(), "voice-shell")
        }
        // Сторож на случай, если движок не отчитается о конце речи.
        main.removeCallbacks(speechWatchdog)
        main.postDelayed(
            speechWatchdog,
            (SPEECH_MARGIN_MS + text.length * SPEECH_PER_CHAR_MS).coerceAtMost(SPEECH_CAP_MS)
        )
    }

    private fun silence() {
        main.removeCallbacks(speechWatchdog)
        runCatching { tts?.stop() }
        runCatching { spare?.stop() }
        pending = null
        speaking = false
        // Оборвали речь сами — значит слушаем дальше, не дожидаясь движка.
        main.postDelayed({ if (!speaking && !awaitingCommand) resumeWake() }, 300)
    }

    // ---------- связь ----------
    private fun connect() {
        reconnectScheduled = false
        if (stopped) return
        if (!prefs.isConfigured) {
            link(LinkState.OFF, Link.hint(this, LinkState.OFF, null, 0))
            return
        }
        if (socket != null) return
        link(LinkState.CONNECTING, getString(R.string.link_connecting_to, prefs.server))
        val request = Request.Builder().url(prefs.socketUrl()).build()
        socket = http.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(
                    JSONObject().put("id", "hello").put("v", 1)
                        .put("token", prefs.token).put("device_id", "android")
                        // Демон отвечает на языке телефона, пока не услышит
                        // другой: иначе первая же реплика пришла бы по-английски.
                        .put("language", prefs.language)
                        .put("app_version", "0.4.0").toString()
                )
                main.post {
                    unauthorized = false
                    attempt = 0
                    link(LinkState.ONLINE, getString(R.string.link_hint_online))
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                main.post { runCatching { onServerMessage(JSONObject(text)) } }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                val code = response?.code ?: 0
                val message = t.message
                // Колбэки okhttp приходят со своего потока: всё, что трогает
                // таймеры и уведомление, делаем на главном.
                main.post { lost(Link.classify(message, code, online()), message, code) }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                // Демон закрыл сам. Неверный токен от этого не исправится, и
                // долбиться в него раз в две секунды бессмысленно.
                main.post {
                    lost(if (unauthorized) LinkState.REFUSED else LinkState.NO_SERVER, reason, 0)
                }
            }
        })
    }

    /** Связь пропала: сказать человеку причину и назначить следующую попытку. */
    private fun lost(state: LinkState, message: String?, httpCode: Int) {
        socket = null
        if (stopped) return
        val reason = Link.hint(this, state, message, httpCode)
        if (reconnectScheduled) {
            link(state, reason)
            return
        }
        val delay = Link.delayMs(state, attempt)
        attempt++
        reconnectScheduled = true
        main.postDelayed(reconnect, delay)
        link(state, getString(R.string.link_retry_in, reason, delay / 1000))
    }

    private val reconnect = Runnable { connect() }

    /** Ждать паузу незачем: человек говорит или сам нажал «повторить». */
    private fun connectNow() {
        main.removeCallbacks(reconnect)
        reconnectScheduled = false
        attempt = 0
        connect()
    }

    private fun link(state: LinkState, text: String) {
        linkState = state
        report(text)
    }

    /**
     * Есть ли вообще сеть.
     *
     * Без этого «нет связи» означало и выпавший wi-fi, и неверный токен, и
     * выключенный демон — а чинить каждый раз надо разное.
     */
    private fun online(): Boolean = runCatching {
        val manager = getSystemService(ConnectivityManager::class.java)
        val network = manager.activeNetwork ?: return@runCatching false
        val caps = manager.getNetworkCapabilities(network)
        caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }.getOrDefault(true)

    private fun onServerMessage(message: JSONObject) {
        when (message.optString("id")) {
            "welcome" -> report(getString(R.string.ready_credential, message.optString("credential")))
            "route" -> onRoute(message)
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
                report(getString(R.string.auth_open_link))
                sendBroadcast(
                    Intent(ACTION_STATUS).setPackage(packageName)
                        .putExtra(EXTRA_TEXT, getString(R.string.auth_link_received))
                        .putExtra(EXTRA_AUTH_URL, url)
                )
            }
            "auth_token" -> {
                val persisted = message.optBoolean("persisted")
                report(
                    getString(
                        if (persisted) R.string.auth_saved else R.string.auth_not_saved
                    )
                )
                sendBroadcast(
                    Intent(ACTION_STATUS).setPackage(packageName)
                        .putExtra(
                            EXTRA_TEXT,
                            getString(
                                if (persisted) R.string.auth_connected
                                else R.string.auth_connected_unsaved
                            )
                        )
                        .putExtra(EXTRA_AUTH_TOKEN, message.optString("token"))
                )
            }
            "auth_error" -> report(getString(R.string.auth_failed, message.optString("message")))
            "whisper" -> speak(message.optString("text"))
            "error" -> {
                val code = message.optString("code")
                if (code == "unauthorized") {
                    unauthorized = true
                    link(LinkState.REFUSED, getString(R.string.error_bad_token))
                } else {
                    report(getString(R.string.error_from_server, message.optString("message")))
                }
            }
        }
    }

    /**
     * Демон сказал, куда ушла реплика — или что не ушла никуда.
     *
     * Отказ по роли раньше просто игнорировался: человек говорил, телефон
     * бодро отвечал «отправил», и на этом всё заканчивалось. Молчание в ответ
     * на команду — худшее, что тут может быть.
     */
    private fun onRoute(message: JSONObject) {
        val reason = message.optString("reason")
        if (reason != "role_gate" && reason != "approval_role_gate") {
            roleGates = 0
            return
        }
        roleGates++
        signals?.missed(route?.onBluetoothMic == true)
        phase(R.string.state_waiting_for_wake)
        report(getString(R.string.role_gate, message.optString("label")))
        // Два отказа подряд — это уже не чужая речь рядом, это врут измерения.
        // Ронять команды молча хуже, чем работать по-старому: выключаем сами и
        // говорим вслух, иначе человек так и не узнает, почему всё ожило.
        if (roleGates >= 2 && prefs.acoustics) {
            prefs.acoustics = false
            roleGates = 0
            report(getString(R.string.role_gate_off))
            // Вслух — на языке реплик, а не интерфейса: голос синтеза
            // выбирается по письменности самого текста, и фраза из ресурсов
            // досталась бы голосу чужого языка. Поэтому произносимое живёт
            // рядом с остальным произносимым, а не в strings.xml.
            speak("Выключил признаки говорящего: демон принимал меня за чужого.")
        }
    }

    private fun send(payload: JSONObject) {
        val ws = socket
        if (ws == null) {
            // Реплика пропала молча — это и есть «он меня не слышит».
            report(getString(R.string.not_sent, getString(linkState.label)))
            // Раз человек говорит, самое время попробовать связаться снова.
            main.post { connectNow() }
            return
        }
        ws.send(payload.toString())
    }

    // ---------- уведомление и статус ----------
    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL, getString(R.string.app_name), NotificationManager.IMPORTANCE_LOW
            )
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
            .setContentTitle(getString(R.string.notification_title, getString(phase)))
            // Фаза говорит, слушает ли телефон; строка связи — дойдёт ли
            // сказанное до демона. Без второй первая обманчива.
            .setSubText(getString(linkState.label))
            .setContentText(text)
            .setContentIntent(open)
            .addAction(0, getString(R.string.action_speak), listen)
            .addAction(0, getString(R.string.action_stop_short), stop)
            .setOngoing(true)
            .setSilent(true)
            .build()
    }

    /** Одно слово о том, что сейчас происходит: его видно с экрана блокировки. */
    private fun phase(next: Int) {
        if (phase == next) return
        phase = next
        report(lastLine)
    }

    private fun report(text: String) {
        Log.i(TAG, text)
        lastLine = text
        main.post {
            runCatching {
                getSystemService(NotificationManager::class.java)
                    .notify(NOTIFICATION_ID, notification(text))
            }
            sendBroadcast(
                Intent(ACTION_STATUS).setPackage(packageName)
                    .putExtra(EXTRA_TEXT, text)
                    .putExtra(EXTRA_LINK, linkState.name)
            )
        }
    }

    private fun fail(reason: String) {
        Log.e(TAG, reason)
        runCatching { prefs.lastError = reason }
        sendBroadcast(Intent(ACTION_STATUS).setPackage(packageName).putExtra(EXTRA_TEXT, reason))
        stopSelf()
    }
}
