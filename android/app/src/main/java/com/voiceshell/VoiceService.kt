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
import android.os.PowerManager
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
        // Локальная модель отпускает микрофон не в тот же миг, когда её
        // остановили: поток записи закрывается чуть позже, и на канале
        // гарнитуры — заметно позже.
        private const val MIC_HANDOVER_MS = 150L
        private const val MIC_RETRY_MS = 250L
        // Одно нажатие система умеет доставить дважды, разными путями.
        private const val BUTTON_DEBOUNCE_MS = 600L
        // Сколько раз пробовать поднять модель обращения, прежде чем жаловаться.
        private const val WAKE_RETRIES = 3
        // Отдельное уведомление: то, что о работе, снимается вместе со службой.
        private const val FAILURE_ID = 43
        // Короче этого одно слово от локальной модели — почти наверняка мусор.
        private const val FALLBACK_MIN_CHARS = 12
        /** Распознаватель обязан ответить хоть чем-то; молчит дольше — он мёртв. */
        private const val RECOGNIZER_DEADLINE_MS = 15_000L
        /** Сколько ждать конца произнесения, если движок забыл сказать «готово». */
        private const val SPEECH_MARGIN_MS = 5_000L
        private const val SPEECH_PER_CHAR_MS = 80L
        private const val SPEECH_CAP_MS = 120_000L
        /** Раз в полминуты проверяем, что нас всё ещё можно позвать. */
        private const val HEARTBEAT_MS = 30_000L
        /**
         * Сколько второе ухо может быть открыто, если о нём забыли.
         *
         * Час — не про батарею одну: открытый микрофон на чужую речь не должен
         * переживать разговор, ради которого его открыли. Закрывается само,
         * молча (человек может быть в середине фразы), но видно в уведомлении,
         * а на вопрос к буферу демон честно ответит, что ухо закрыто.
         */
        private const val EAR_MAX_MS = 60 * 60 * 1000L
        /**
         * Сколько кругов облачного распознавания подряд позволено комнате.
         *
         * Пока в комнате говорят, круги идут один за другим — иначе каждая
         * вторая фраза терялась бы в паузе. Но телевизор или кафе говорят без
         * остановки часами, и тогда этот счётчик возвращает микрофон модели
         * обращения: следующий круг начнётся только с новой речи.
         */
        private const val EAR_CHAIN_MAX = 20
        /** Столько пустых кругов подряд означают, что в комнате замолчали. */
        private const val EAR_IDLE_MAX = 2
        /** Хвост собственной реплики: столько после неё комнату не слушаем. */
        private const val EAR_ECHO_GAP_MS = 1_200L
        /** Сколько тишины считать концом чужой фразы. */
        private const val EAR_SILENCE_MS = 1_500L
        /**
         * Пауза перед новым кругом там, где начать его больше некому.
         *
         * Круг комнаты обычно начинается с речи, которую услышала локальная
         * модель. Если она на этом телефоне не поднялась, сказать «вокруг
         * заговорили» некому, и ухо возвращается к микрофону само, выждав
         * паузу. Это дороже — и это единственный способ, которым второе ухо
         * там вообще работает.
         */
        private const val EAR_RETRY_MS = 5_000L
        /** Столько длится один круг, если рядом говорят без пауз. */
        private const val EAR_ROUND_MS = 60_000L
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
    // Когда нажатие гарнитуры последний раз что-то сделало.
    private var buttonActedAt = 0L
    // Рукопожатие завершено и hello ушло: до этого сокет есть, а собеседника нет.
    @Volatile private var ready = false
    private var wakeLock: PowerManager.WakeLock? = null
    @Volatile private var audioFocus = false
    private var cloud: SpeechRecognizer? = null
    private var wake: WakeWordEngine? = null
    private var route: AudioRoute? = null
    private var signals: Signals? = null
    /** Громкость реплики: единственное, что телефон может измерить сам. */
    private val meter = SpeechMeter()
    /** Что происходит прямо сейчас — первая строка уведомления. */
    @Volatile private var phase = R.string.state_waiting_for_wake

    // Синтез зовёт колбэки со своего потока, а читают эти поля с главного.
    // Без пометки главный поток мог видеть их устаревшими до полусекунды —
    // ровно столько, сколько нужно, чтобы уронить ответную реплику человека
    // в буфер чужой речи или пропустить её вовсе.
    @Volatile private var speaking = false
    private var awaitingCommand = false
    private var lastSpoken = ""
    @Volatile private var lastLine = ""
    @Volatile private var spokeAt = 0L
    @Volatile private var windowUntil = 0L
    @Volatile private var wakeReady = false
    private var fallbackText = ""
    private var unauthorized = false

    /** Сколько реплик подряд демон отверг как чужую речь. */
    private var roleGates = 0

    // ---------- что помнит второе ухо ----------
    /** Распознаватель комнаты: у него свой язык и свой круг жизни. */
    private var roomEar: SpeechRecognizer? = null
    private var roomListening = false
    // Круг уже назначен, но ещё не начался: между отбором микрофона и
    // стартом есть пауза, и в неё успевает вклиниться что угодно.
    private var roomStarting = false
    /** Кругов подряд без возврата микрофона модели обращения. */
    private var roomChain = 0
    /** Пустых кругов подряд: столько тишины — и микрофон отдаём обратно. */
    private var roomIdle = 0
    /** Сначала пробуем распознавание на устройстве: оно не стоит ни трафика,
     *  ни квоты. Движок отказался — переходим в сеть и говорим об этом раз. */
    private var roomOffline = true
    private var roomOfflineSaid = false
    /** Об отказавшем распознавателе говорим раз на открытое ухо, а не раз в круг. */
    private var roomErrorSaid = false

    private var linkState = LinkState.OFF
    private var attempt = 0
    private var reconnectScheduled = false
    /** Служба уже остановлена: отложенные попытки связи должны умереть вместе с ней. */
    @Volatile private var stopped = false

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        // Пока запись идёт, процессор не спит сам собой. Но сторожа нужны
        // как раз тогда, когда запись умерла: телефон в кармане засыпает, и
        // ни сердцебиение, ни переподключение не тикают до следующего
        // касания экрана. Вся починка переставала работать ровно в том
        // случае, ради которого писалась.
        runCatching {
            wakeLock = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "voice-shell:listening")
                .also { it.setReferenceCounted(false); it.acquire() }
        }
        lastLine = getString(R.string.state_starting)
        // Второе ухо не переживает запуск службы. `onDestroy` гасит флаг сам,
        // но процесс могли убить и мимо него — а тогда телефон снова слушал
        // бы чужой разговор, о котором его уже никто не просил, и ещё и без
        // часового предела: он живёт в отложенном вызове, а не в настройках.
        prefs.secondEar = false

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
            // Своим текстом прошлую ошибку не затираем: сюда приходят и
            // случайные подъёмы — например, кнопкой гарнитуры по выключенной
            // службе, — а под ними лежит настоящая причина смерти.
            if (prefs.lastError.isBlank()) {
                fail(getString(R.string.error_service_start,
                               "${t.javaClass.simpleName}: ${t.message}"))
            } else {
                Log.e(TAG, "startForeground", t)
                stopSelf()
            }
            return
        }

        try {
            setUpTts()
            setUpMediaSession()
            connect()
            // Прежнюю ошибку не стираем: служба перезапускается сама через
            // секунды после падения, и стирание здесь означало, что о ночном
            // падении человек не узнает никогда. Её чистит экран, когда её
            // прочитали.
            prefs.keepPreviousError()
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
            main.post {
                if (socket == null && linkState != LinkState.OFF) connectNow()
                // Модель обращения качается сорок пять мегабайт, и первая
                // попытка часто приходится на дорогу. Сеть вернулась — самое
                // время попробовать снова: иначе телефон отзывался бы только
                // на кнопку до следующего запуска службы.
                if (!wakeReady && !preparingWake) Thread { prepareWakeWord() }.start()
            }
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
        // Второе ухо не переживает приложение: спека требует стирать буфер
        // при выходе, а открытый микрофон на чужую речь тем более не должен
        // молча дождаться следующего запуска. Демону говорим, пока сокет жив.
        if (prefs.secondEar) {
            runCatching { prefs.secondEar = false }
            send(JSONObject().put("id", "ambient_control").put("submode", "off").put("wipe", true))
        }
        main.removeCallbacksAndMessages(null)
        runCatching {
            getSystemService(ConnectivityManager::class.java)
                .unregisterNetworkCallback(networkWatch)
        }
        runCatching { wakeLock?.release() }
        wakeLock = null
        runCatching { signals?.release() }
        runCatching { route?.release() }
        runCatching { wake?.release() }
        runCatching { cloud?.destroy() }
        runCatching { roomEar?.destroy() }
        runCatching { tts?.shutdown() }
        runCatching { spare?.shutdown() }
        // Уведомление снимаем сами: движки, умирая, ещё раз зовут колбэки,
        // и их отчёт мог бы поднять его заново — уже без службы за ним.
        runCatching {
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
        }
        runCatching { session?.release() }
        runCatching { socket?.close(1000, "service stopped") }
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ---------- wake word (необязательный) ----------
    @Volatile private var preparingWake = false

    private fun prepareWakeWord() {
        if (preparingWake) return
        preparingWake = true
        try {
            val engine = WakeWordEngine(this)
            engine.prepare { report(it) }
            // Пока модель качалась и грузилась — десятки секунд на первом
            // запуске, — службу могли остановить. Ссылки на движок ещё ни у
            // кого не было, поэтому `onDestroy` его не освободил бы: он
            // открыл бы микрофон уже мёртвой службы и держал бы его до
            // смерти процесса. Следующий запуск при этом ничего не слышит.
            if (stopped) {
                runCatching { engine.release() }
                return
            }
            // Ссылка — до старта, а не после: между ними телефон уже
            // записывает, и `wake?.stop()` с главного потока бьёт в null.
            wake = engine
            engine.start(
                onText = { text -> main.post { handle(text) } },
                onError = { t -> main.post { wakeDied(t) } }
            )
            wakeReady = true
            if (stopped) {
                runCatching { engine.release() }
                return
            }
            report(getString(R.string.state_listening_for_wake))
        } catch (t: Throwable) {
            // Самый частый случай: не поднялась нативная библиотека.
            Log.e(TAG, "wake word", t)
            wakeReady = false
            report(getString(R.string.wake_unavailable, t.javaClass.simpleName))
        } finally {
            preparingWake = false
        }
    }

    /**
     * Маршрут микрофона сменился: гарнитуру надели, сняли или канал переоткрыли.
     * Уже открытый поток записи остался на старом устройстве — перезапускаем.
     */
    private fun onRouteChanged(why: String) {
        Log.i(TAG, "route: $why")
        if (speaking || awaitingCommand) return
        // Круг комнаты открыт на прежнем микрофоне, и переезжать сам он не
        // умеет — как и распознавание обращения. Следующий круг начнётся уже
        // на новом, с первой же услышанной фразы.
        stopRoom()
        if (!wakeReady) return
        // Дальше — про модель обращения; комнату мы уже вернули на место.
        runCatching { wake?.stop() }
        resumeWake()
    }

    // ---------- разбор реплики ----------
    private fun handle(text: String) {
        if (isOwnEcho(text)) return
        // «Стоп» — единственная команда без обращения.
        //
        // Она такой задумана: остановка не должна зависеть ни от круга по
        // сети, ни от того, расслышали ли обращение. Все остальные разборы
        // стояли здесь же — и это была дыра, а не решение. Локальная модель
        // отдаёт сюда ВСЮ услышанную речь, не только обращения: разговор в
        // комнате шёл через четыре таблицы команд, и «второе ухо это
        // метафора» открывало микрофон на комнату, а «английский там лучше»
        // молча закрепляло язык ответа. Остальное — после проверки, что
        // говорили нам.
        Intents.stopIntent(text)?.let { scope ->
            silence()
            send(JSONObject().put("id", "interrupt").put("scope", scope))
            report(getString(if (scope == "work") R.string.stopping_work else R.string.going_quiet))
            return
        }
        if (speaking) {
            // Обращение посреди ответа — это перебивание. Раньше оно молча
            // отбрасывалось, и выглядело это как «отзывается через раз».
            if (!Intents.hasWake(text)) return
            silence()
        }
        val windowOpen = System.currentTimeMillis() < windowUntil
        if (!windowOpen && !Intents.hasWake(text)) {
            // Это говорили не нам. Пока ухо закрыто — дальше и не идём, как
            // было всегда. Открыто — значит рядом идёт разговор, и вот он:
            // локальная модель услышала речь, а расслышать её как следует,
            // да ещё на языке комнаты, может только распознаватель телефона.
            if (prefs.secondEar) listenToRoom()
            return
        }
        windowUntil = 0

        // Обращение услышано — теперь можно и команды разбирать.
        Intents.secondEar(text)?.let { change ->
            secondEarCommand(change, text)
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
    /**
     * Наше ли это эхо.
     *
     * Окно отсчитывалось от НАЧАЛА реплики и истекало через двенадцать
     * секунд. Ответ длиннее этого перебивал сам себя: на канале гарнитуры
     * микрофон и динамик делят одну узкую линию, эхо там сильнее всего, и
     * одно «Клод», расслышанное в собственном голосе, обрывало ответ на
     * полуслове. Пока говорим — эхо есть по определению.
     */
    private fun isOwnEcho(text: String): Boolean {
        if (lastSpoken.isBlank()) return false
        if (!speaking && System.currentTimeMillis() - spokeAt > 12_000) return false
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
        // Огрызок в одно слово — это не реплика, а то, что локальная модель
        // успела поймать до передачи микрофона. Отправлять его значит
        // получить в ухо «повтори, пожалуйста, что нужно?» — круг к модели,
        // секунды и ощущение, что оболочка не слышит. Лучше сразу сказать,
        // что не расслышали, и слушать снова.
        val words = text.trim().split(Regex("\\s+"))
        if (words.size < 2 && text.trim().length < FALLBACK_MIN_CHARS) {
            report(getString(R.string.heard_too_little, text))
            signals?.missed(route?.onBluetoothMic == true)
            phase(R.string.state_waiting_for_wake)
            return
        }
        report(getString(R.string.heard_locally, text))
        deliver(text)
    }

    private fun deliver(
        payload: String,
        alternatives: List<String> = emptyList(),
        measured: Segment? = null,
    ) {
        if (payload.isBlank()) return
        val device = if (route?.onBluetoothMic == true) "sony_mic" else "phone_mic"
        // Сигнал «принято» — после отправки, а не до неё.
        //
        // Раньше он звучал первым: человек в наушниках слышал подтверждение,
        // а реплика в это время не уходила никуда. Положительный звук на
        // потерянной реплике — худшее, что можно ему сообщить.
        val sent = send(
            JSONObject()
                .put("id", "speech_segment")
                .put("segment_id", System.currentTimeMillis().toString())
                .put("transcript", payload)
                // Закрепление языка едет с каждой репликой: переподключение
                // иначе тихо его снимает, и ответ приходит на чужом языке.
                .put("reply_language", prefs.replyLanguage)
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
        if (sent) {
            signals?.accepted(route?.onBluetoothMic == true)
            phase(R.string.state_sent)
            report(getString(R.string.sent_to, payload))
        }
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
        // Без оговорки про `speaking`: половина путей `say()` выходит, так и
        // не начав говорить, — и слушатель остаётся выключенным, потому что
        // выключают его до синтеза. Сторож обязан возвращать слух в обоих
        // случаях, а не только когда речь оборвалась на полуслове.
        val wasSpeaking = speaking
        speaking = false
        spokeAt = System.currentTimeMillis()
        windowUntil = System.currentTimeMillis() + WINDOW_MS
        if (wasSpeaking) report(getString(R.string.tts_silent))
        if (!awaitingCommand) resumeWake()
    }

    /**
     * Последняя линия обороны: раз в полминуты проверяем, что нас можно
     * позвать. Любая причина, по которой слушатель встал, лечится одинаково.
     */
    private val heartbeat = object : Runnable {
        override fun run() {
            // Пока микрофон держит комната, модель обращения и не должна
            // работать: без этой оговорки heartbeat раз в полминуты решал бы,
            // что слушатель встал, и обрывал чужой разговор на полуслове.
            if (wakeReady && !speaking && !awaitingCommand && !roomListening &&
                wake?.isRunning != true
            ) {
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

    /**
     * Что сказать распознавателю про язык — одинаково обоим ушам.
     *
     * Языка два: реплику слушаем на языке микрофона, комнату — на её
     * собственном, и это разные вызовы с разным `language`. А вот просьба к
     * распознавателю определять и переключать язык самому — общая, и живёт
     * она здесь в одном экземпляре: два блока разъехались бы на первой же
     * правке, и разъехались бы молча.
     *
     * Здесь уже стояла попытка это включить, и она не работала: значение
     * «adaptive» такого API не бывает — валидные это high_precision, balanced,
     * quick_response, — а переключение языков без включённого их определения
     * не работает вовсе. Отсюда и «на иврите не слышит».
     *
     * Даже так это «по возможности»: extras исполняет служба распознавания, и
     * не всякая их поддерживает. Не поддержала — молча слушает один язык.
     */
    private fun hearing(intent: Intent, language: String) {
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, language)
        if (!prefs.multilingual || Build.VERSION.SDK_INT < 33) return
        // Язык микрофона в списке всегда: на нём зовут по имени, и ухо
        // комнаты обязано узнать обращение, пока держит микрофон.
        val allowed = ArrayList(listOf(language) + Prefs.SUPPORTED.filter { it != language })
        intent.putExtra(RecognizerIntent.EXTRA_ENABLE_LANGUAGE_DETECTION, true)
        intent.putStringArrayListExtra(
            RecognizerIntent.EXTRA_LANGUAGE_DETECTION_ALLOWED_LANGUAGES, allowed)
        intent.putExtra(RecognizerIntent.EXTRA_ENABLE_LANGUAGE_SWITCH,
                        RecognizerIntent.LANGUAGE_SWITCH_BALANCED)
        intent.putStringArrayListExtra(
            RecognizerIntent.EXTRA_LANGUAGE_SWITCH_ALLOWED_LANGUAGES, allowed)
    }

    private fun listenForCommand(fallback: String = "") {
        if (awaitingCommand) return
        awaitingCommand = true
        fallbackText = fallback
        main.post {
            // Копилка громкости — только про эту реплику: кадры прошлой
            // сделали бы «фоном» чужой голос из прошлого разговора.
            meter.reset()
            // Реплика важнее комнаты: микрофон отбираем у неё, не дожидаясь
            // конца круга, иначе команда уедет в буфер как чужая речь.
            stopRoom()
            runCatching { wake?.stop() }
            startCloud(attempt = 1)
        }
    }

    /**
     * Запустить облачное распознавание, отдав микрофон по-настоящему.
     *
     * `wake?.stop()` возвращает управление раньше, чем освобождается сам
     * поток записи, — особенно на канале bluetooth-гарнитуры. Старт впритык
     * упирался в занятый микрофон и падал с ERROR_CLIENT: в логе это
     * выглядело как «ошибка 5», а в ухе — как «в шумном месте не слышит»,
     * потому что до сервера доходил только огрызок от локальной модели.
     */
    private fun startCloud(attempt: Int) {
        main.postDelayed({
            if (!awaitingCommand) return@postDelayed
            try {
                if (cloud == null) cloud = SpeechRecognizer.createSpeechRecognizer(this)
                val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
                    // Несколько гипотез: распознаватель почти всегда держит
                    // верный вариант вторым, когда путает имя из проекта.
                    putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5)
                    // Реплика — на языке микрофона, что бы ни слушала комната.
                    hearing(this, prefs.language)
                }
                cloud?.setRecognitionListener(commandListener(attempt))
                cloud?.startListening(intent)
                if (fallbackText.isNotBlank()) main.postDelayed(fallbackTimer, FALLBACK_MS)
                main.postDelayed(recognizerWatchdog, RECOGNIZER_DEADLINE_MS)
            } catch (t: Throwable) {
                awaitingCommand = false
                report(getString(R.string.recognition_unavailable, t.javaClass.simpleName))
                deliverFallback()
                resumeWake()
            }
        }, if (attempt == 1) MIC_HANDOVER_MS else MIC_RETRY_MS)
    }

    /**
     * Ошибка, означающая «микрофон ещё не отдали», а не «не расслышал».
     *
     * `cancel()` у распознавателя — асинхронный вызов в чужой процесс, и
     * пока та сессия не свернулась, канонический ответ системы — BUSY, а не
     * CLIENT. AUDIO прилетает после смены маршрута — это та же беда.
     */
    private fun busyMic(error: Int): Boolean =
        error == SpeechRecognizer.ERROR_CLIENT ||
            error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY ||
            error == SpeechRecognizer.ERROR_AUDIO

    private fun commandListener(attempt: Int = 1) = object : CloudListener {
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
            // Тот же порядок, что и у локальной модели: «хватит слушать
            // вокруг» начинается со слова, которым гасят голос.
            Intents.secondEar(text)?.let { change ->
                secondEarCommand(change, text)
                return
            }
            Intents.stopIntent(text)?.let { scope ->
                silence()
                send(JSONObject().put("id", "interrupt").put("scope", scope))
                return
            }
            Intents.listenSwitch(text)?.let { change ->
                // Этого разбора здесь не было вовсе, хотя комментарий выше
                // обещал «тот же порядок». А это и есть обычный путь:
                // обращение слышит локальная модель, реплику после него —
                // облачный распознаватель. Смена языка микрофона работала
                // только тогда, когда маленькая русская модель случайно
                // разбирала всю фразу сама.
                switchListening(change)
                return
            }
            Intents.languageSwitch(text)?.let { code ->
                switchReplyLanguage(code, aloud = false)
                return
            }
            deliver(text, heard.drop(1).take(3), meter.segment())
        }

        override fun onError(error: Int) {
            // Занятый микрофон — не «не расслышал», а «не успели отдать».
            // Один повтор через четверть секунды: к этому времени поток
            // записи локальной модели закрыт наверняка. Отдавать вместо
            // этого огрызок от неё — значит слать серверу «меня» и получать
            // «повтори, пожалуйста», потратив круг и время человека.
            if (busyMic(error) && attempt == 1) {
                Log.i(TAG, "recognizer busy, one more try")
                main.removeCallbacks(recognizerWatchdog)
                main.removeCallbacks(fallbackTimer)
                runCatching { cloud?.cancel() }
                startCloud(attempt = 2)
                return
            }
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
        main.postDelayed({ if (!awaitingCommand && !speaking) resumeWake() }, 300)
    }

    /**
     * Модель обращения умерла — поднять её заново.
     *
     * Умирает она от того же, от чего всё в этом слое: у неё отобрали
     * микрофон. Входящий звонок, ассистент, камера, обрыв канала гарнитуры.
     * Раньше об этом писалась строчка в уведомление, и на этом всё
     * заканчивалось — телефон оставался глухим до перезапуска службы.
     */
    private fun wakeDied(t: Throwable) {
        report(getString(R.string.wake_error, t.message.orEmpty()))
        runCatching { wake?.stop() }
        main.postDelayed({ resumeWake() }, MIC_RETRY_MS)
    }

    private fun resumeWake(attempt: Int = 1) {
        if (!wakeReady) return
        // Микрофон занят комнатой: отнимать его посреди чужой фразы нельзя, а
        // отнял бы кто угодно — сторож синтеза, смена маршрута, heartbeat.
        if (roomListening) return
        runCatching {
            wake?.start(
                onText = { text -> main.post { handle(text) } },
                onError = { t -> main.post { wakeDied(t) } }
            )
        }.onFailure {
            // Микрофон ещё держит прежний хозяин: `cancel` у распознавателя
            // асинхронный, и поток записи закрывается позже. Раньше здесь
            // писалась строчка и всё — до полуминуты глухоты, пока не придёт
            // сторож. Пробуем сами, пару раз, с той же паузой, что и везде.
            if (attempt < WAKE_RETRIES) {
                main.postDelayed({ resumeWake(attempt + 1) }, MIC_RETRY_MS)
            } else {
                report(getString(R.string.wake_stopped, it.javaClass.simpleName))
            }
        }
    }

    // ---------- второе ухо ----------
    /**
     * Открыть или закрыть второе ухо на самой трубке.
     *
     * Флаг живёт в настройках, а не в памяти службы, потому что службу
     * перезапускает система; но пережить остановку приложения он не должен —
     * это делает `onDestroy`.
     */
    private fun setEar(open: Boolean, line: Int) {
        val changed = open != prefs.secondEar
        prefs.secondEar = open
        main.removeCallbacks(earTimeout)
        if (open) {
            // Новый разговор — новая попытка обойтись распознаванием на
            // устройстве: прошлый отказ мог быть про другой язык комнаты.
            roomOffline = true
            roomOfflineSaid = false
            roomErrorSaid = false
            main.postDelayed(earTimeout, EAR_MAX_MS)
            // Там, где локальной модели нет, первый круг тоже некому начать.
            roomLater()
        } else {
            stopRoom()
            if (!speaking && !awaitingCommand) resumeWake()
        }
        // Индикатор обязателен по спеке и не для красоты: телефон слушает
        // других людей. `report` перерисовывает уведомление, а в нём уже
        // стоит пометка про открытое ухо.
        if (changed) report(getString(line))
    }

    /**
     * «Клод, второе ухо» · «выключи второе ухо» · «второе ухо на иврите».
     *
     * Телефон переключает себя сам и сразу, не дожидаясь круга по сети: иначе
     * «перестань слушать» означало бы «перестань через полсекунды, если
     * связь жива». А саму фразу отдаём демону как обычную реплику — он
     * ответит вслух, назовёт согласие собеседников и разошлёт своё состояние.
     * Своего подтверждения телефон не говорит: два ответа на одну команду
     * хуже, чем один.
     */
    private fun secondEarCommand(change: Intents.SecondEar, said: String) {
        change.language?.let { code ->
            prefs.ambientLanguage = code
            // Уже начатый круг языка не переслушает, а extras достаются
            // распознавателю при старте — поэтому клиента пересоздаём.
            stopRoom()
            runCatching { roomEar?.destroy() }
            roomEar = null
            report(getString(R.string.second_ear_language, code))
        }
        setEar(change.open,
               if (change.open) R.string.second_ear_open else R.string.second_ear_closed)
        deliver(said)
    }

    /**
     * Час прошёл, а ухо всё открыто.
     *
     * Молча — потому что человек может быть в середине разговора, и голос в
     * ухе посреди чужой фразы хуже, чем закрытое ухо. Видно в уведомлении, а
     * на вопрос к буферу демон сам ответит, что ухо закрыто.
     */
    private val earTimeout = Runnable {
        if (prefs.secondEar) {
            setEar(false, R.string.second_ear_expired)
            send(JSONObject().put("id", "ambient_control").put("submode", "off").put("wipe", true))
        }
    }

    /**
     * Послушать комнату — один круг распознавания.
     *
     * Круг начинается только после того, как локальная модель услышала речь:
     * в тихой комнате открытое ухо не стоит ничего сверх обычного дня, и это
     * главное, чем оплачен непрерывный режим. Пока в комнате говорят, круги
     * идут один за другим — иначе каждая вторая фраза терялась бы в паузе
     * между ними.
     */
    private fun listenToRoom(): Boolean {
        if (!prefs.secondEar || roomListening || awaitingCommand || speaking) return false
        // Слать некуда — значит и слушать чужой разговор незачем: буфер живёт
        // у демона, а `send` без связи только копил бы попытки достучаться.
        if (socket == null) return false
        // Хвост собственной реплики ещё звучит в комнате.
        if (System.currentTimeMillis() - spokeAt < EAR_ECHO_GAP_MS) return false
        if (roomChain >= EAR_CHAIN_MAX) {
            // Телевизор в комнате говорит часами. Микрофон возвращается
            // модели обращения, а следующий круг начнётся с новой речи.
            roomChain = 0
            return false
        }
        // Флаг поднимается там же, где круг начинается, а не раньше.
        //
        // Раньше между ним и стартом была отложенная задача, и всё, что
        // успевало вклиниться, — нажатие кнопки, ответ демона — гасило флаг,
        // а круг всё равно стартовал. Получался распознаватель, о котором
        // никто не знает: остановить его нечем, сторожа у него нет, а
        // микрофон он держит вечно. Вместе с микрофоном пропадало и слово
        // «Клод» — навсегда, до перезапуска службы.
        roomStarting = true
        // Микрофон отдаём сейчас, круг начинаем позже: модель обращения
        // закрывает поток записи не в тот же миг, и старт впритык упирался
        // в занятый микрофон — та же ошибка 5, что и на командном пути,
        // только здесь она гасила каждый круг второго уха, а жаловалась
        // один раз за открытое ухо.
        runCatching { wake?.stop() }
        startRoom(attempt = 1)
        return true
    }

    /** Сам круг: микрофон уже отобран, осталось дождаться, пока его отпустят. */
    private fun startRoom(attempt: Int) {
        main.postDelayed({
            roomStarting = false
            if (!prefs.secondEar || roomListening || awaitingCommand || speaking) {
                // Микрофон мы уже отобрали, а круга не будет: вернуть его
                // модели обращения обязаны мы, больше некому.
                if (!awaitingCommand && !speaking) resumeWake()
                return@postDelayed
            }
            roomListening = true
            try {
                if (roomEar == null) roomEar = SpeechRecognizer.createSpeechRecognizer(this)
                val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                             RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
                    // Гипотеза одна: чужую реплику никто не переписывает, её
                    // только пересказывают, и вторая догадка тут не помощь.
                    putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                    putExtra(
                        RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS,
                        EAR_SILENCE_MS
                    )
                    // Распознавание на устройстве, пока движок его тянет:
                    // час чужого разговора через сеть — это и батарея, и квота.
                    putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, roomOffline)
                    hearing(this, prefs.ambientLanguage)
                }
                roomEar?.setRecognitionListener(roomListener(attempt))
                roomEar?.startListening(intent)
                roomChain++
                phase(R.string.state_room)
                // Пока распознаватель не отозвался, срок у него короткий:
                // мёртвый круг держит микрофон, а с ним и слово «Клод».
                main.postDelayed(roomWatchdog, RECOGNIZER_DEADLINE_MS)
            } catch (t: Throwable) {
                roomListening = false
                report(getString(R.string.recognition_unavailable, t.javaClass.simpleName))
                giveMicBack()
            }
        }, if (attempt == 1) MIC_HANDOVER_MS else MIC_RETRY_MS)
    }

    /**
     * Продолжить слушать комнату — или вернуть микрофон.
     *
     * Круг кончился, а следующий может и не начаться: связь отвалилась, счёт
     * кругов подряд упёрся в потолок, хозяин заговорил сам. Тогда микрофон
     * обязан вернуться модели обращения сразу, а не через полминуты, когда
     * его хватится heartbeat: полминуты без wake word выглядят как «оглох».
     */
    private fun keepListening() {
        if (!listenToRoom()) giveMicBack()
    }

    /** Распознаватель комнаты замолчал совсем — микрофон обратно. */
    /**
     * Круг затянулся: человек рядом говорит без остановки.
     *
     * Останавливаем, а не отменяем: остановка отдаёт расслышанное, отмена
     * выбрасывает его — а это целая минута чужого разговора, ради которой
     * ухо и открывали. Длинная речь так разрежется на куски по минуте, и
     * каждый кусок дойдёт до буфера.
     */
    private val roomWatchdog = Runnable {
        if (roomListening) {
            // Круг, который не отозвался вовсе, остановка тоже расшевелит:
            // разбираться, что именно с ним не так, микрофону не поможет.
            runCatching { roomEar?.stopListening() }
            main.postDelayed(roomGiveUp, RECOGNIZER_DEADLINE_MS)
        }
    }

    /** А вот теперь распознаватель точно мёртв: микрофон нужен кому-то ещё. */
    private val roomGiveUp = Runnable {
        if (roomListening) {
            runCatching { roomEar?.cancel() }
            finishRoom()
            giveMicBack()
        }
    }

    private fun roomListener(attempt: Int = 1) = object : CloudListener {
        override fun onResults(results: Bundle?) {
            val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                .orEmpty().map { it.trim() }.firstOrNull { it.isNotEmpty() }.orEmpty()
            finishRoom()
            if (text.isEmpty()) { roomFellSilent(); return }
            // Собственный ответ, вернувшийся через динамик. В буфере он был бы
            // и ложью («это сказали вокруг»), и мусором: Claude пересказал бы
            // человеку его же вопрос.
            if (isOwnEcho(text)) { keepListening(); return }
            // Пока ухо держит микрофон, оно единственное, что слышит комнату,
            // — и обращение тоже. Поэтому обращённое к нам разбирается здесь,
            // а не уходит в буфер как чужая речь.
            Intents.secondEar(text)?.let { change ->
                secondEarCommand(change, text)
                return
            }
            if (Intents.hasWake(text)) {
                stopRoom()
                // Фразу уже сказали целиком: держим её запасным вариантом,
                // как и при обращении, расслышанном локальной моделью.
                listenForCommand(Intents.stripWake(text))
                return
            }
            roomIdle = 0
            overhear(text)
            keepListening()
        }

        override fun onError(error: Int) {
            finishRoom()
            // Движок не умеет этот язык без сети. Спорить незачем: дальше
            // слушаем через сеть и говорим об этом один раз за открытое ухо.
            if (roomOffline && (error == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE ||
                                error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED)) {
                roomOffline = false
                if (!roomOfflineSaid) {
                    roomOfflineSaid = true
                    report(getString(R.string.second_ear_online, prefs.ambientLanguage))
                }
                keepListening()
                return
            }
            if (error == SpeechRecognizer.ERROR_NO_MATCH ||
                error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT
            ) {
                roomFellSilent()
                return
            }
            // Микрофон ещё не отдали — это не повод бросать круг.
            if (busyMic(error) && attempt == 1) {
                Log.i(TAG, "room recognizer busy, one more try")
                runCatching { roomEar?.cancel() }
                roomStarting = true
                startRoom(attempt = 2)
                return
            }
            // Оборванная сеть, отозванное разрешение: чинить это кругами
            // нельзя, и микрофон честнее отдать обратно.
            // Сказать об этом хватит одного раза: там, где локальной модели
            // нет, круг повторяется сам, и жалоба повторялась бы с ним.
            if (!roomErrorSaid) {
                roomErrorSaid = true
                report(getString(R.string.recognition_error, error))
            }
            giveMicBack()
        }

        // Ни громкость, ни начало речи здесь не считаются нарочно: копилка
        // громкости меряет норму хозяина на этом микрофоне, и чужие голоса
        // сдвинули бы её так, что хозяин перестал бы быть похож на себя.
        /**
         * Распознаватель жив и слушает — можно дать кругу полный срок.
         *
         * Единственное, зачем здесь этот колбэк: ни громкость, ни начало
         * речи не считаются нарочно (см. ниже).
         */
        override fun onReadyForSpeech(params: Bundle?) {
            main.removeCallbacks(roomWatchdog)
            main.postDelayed(roomWatchdog, EAR_ROUND_MS)
        }

        override fun onBeginningOfSpeech() = Unit
        override fun onRmsChanged(rmsdB: Float) = Unit
        override fun onBufferReceived(buffer: ByteArray?) = Unit
        override fun onEndOfSpeech() = Unit
        override fun onPartialResults(partialResults: Bundle?) = Unit
        override fun onEvent(eventType: Int, params: Bundle?) = Unit
    }

    private fun finishRoom() {
        roomListening = false
        main.removeCallbacks(roomWatchdog)
        main.removeCallbacks(roomGiveUp)
    }

    /** Круг прошёл впустую: пара таких подряд — и в комнате точно замолчали. */
    private fun roomFellSilent() {
        roomIdle++
        if (roomIdle >= EAR_IDLE_MAX) giveMicBack() else keepListening()
    }

    /** Микрофон возвращается модели обращения: её дело — услышать «Клод». */
    private fun giveMicBack() {
        // Круг кончился вместе с чередой: потолок считает подряд идущие
        // круги, а не все за день, иначе разговор после паузы упирался бы
        // в потолок, набранный полчаса назад.
        roomChain = 0
        roomIdle = 0
        phase(R.string.state_waiting_for_wake)
        // С паузой: прежний хозяин микрофона — распознаватель, а он
        // закрывает поток записи не мгновенно. Без неё модель обращения не
        // поднималась после каждого круга, и до получаса тишины «Клод» не
        // слышал никто, пока не приходил сторож.
        main.postDelayed({ if (!speaking && !awaitingCommand) resumeWake() }, MIC_HANDOVER_MS)
        roomLater()
    }

    /**
     * Напомнить себе вернуться к комнате.
     *
     * Нужно ровно там, где локальной модели нет: она и есть то, что говорит
     * «вокруг заговорили», и без неё круг комнаты, однажды прерванный
     * репликой хозяина или сменой микрофона, не начался бы больше никогда.
     * Возвращаться мешает всё подряд — идёт команда, звучит ответ, — поэтому
     * попытка повторяется, а не делается один раз: `keepListening` сам
     * назначит следующую, если сейчас нельзя.
     */
    private fun roomLater() {
        if (!prefs.secondEar || wakeReady) return
        main.removeCallbacks(roomAgain)
        main.postDelayed(roomAgain, EAR_RETRY_MS)
    }

    private val roomAgain = Runnable { keepListening() }

    private fun stopRoom() {
        main.removeCallbacks(roomAgain)
        roomStarting = false
        if (roomListening) runCatching { roomEar?.cancel() }
        finishRoom()
        roomChain = 0
        roomIdle = 0
        if (phase == R.string.state_room) phase(R.string.state_waiting_for_wake)
        // Ухо закрывают до `stopRoom`, поэтому здесь это уже не «закрыли», а
        // «микрофон понадобился кому-то ещё» — и к комнате надо вернуться.
        roomLater()
    }

    /**
     * Услышанное вокруг уходит демону — и больше никуда.
     *
     * Отдельно от `deliver` нарочно, и дело не в одном поле. Такая реплика не
     * маршрутизируется и не исполняется, поэтому её не сопровождают ни
     * earcon принятой команды, ни признаки говорящего: признаки меряются
     * относительно нормы хозяина на этом микрофоне, и чужой голос, попавший
     * в норму, сделал бы хозяина непохожим на себя.
     */
    private fun overhear(text: String) {
        val device = if (route?.onBluetoothMic == true) "sony_mic" else "phone_mic"
        send(
            JSONObject()
                .put("id", "speech_segment")
                .put("segment_id", System.currentTimeMillis().toString())
                .put("transcript", text)
                .put("device", device)
                .put("narrowband", route?.onBluetoothMic == true)
                // Роль — не догадка акустики, а факт: этого нам не говорили.
                .put("role", "bystander")
                .put("role_source", "hint")
                .put("ambient", true)
        )
    }

    /**
     * Демон сказал, что стало с его буфером.
     *
     * Состояний двое — ухо телефона и буфер демона, — и разойтись им нельзя:
     * открытое ухо при закрытом буфере шлёт чужую речь в никуда, а закрытое
     * при открытом означает, что человек попросил перестать, а его слушают.
     * Поэтому рассылка демона побеждает: он один знает, что с буфером.
     */
    private fun onAmbient(message: JSONObject) {
        val open = message.optString("submode", "off") != "off"
        if (open == prefs.secondEar) return
        setEar(open, if (open) R.string.second_ear_open else R.string.second_ear_closed)
    }

    /**
     * Связь поднялась заново, а ухо так и осталось открытым.
     *
     * Демон мог перезапуститься с закрытым буфером — тогда чужая речь уходила
     * бы ему и молча пропадала, а человек услышал бы «вокруг я ничего не
     * слышал» в ответ на вопрос о разговоре, который шёл при нём. Просим
     * открыть буфер обратно; вслух об этом не говорим — согласие уже
     * назвали, когда ухо открывали, и повторять его на каждый обрыв связи
     * значит превратить его в шум.
     */
    private fun restoreEar(welcome: JSONObject) {
        val daemonOpen = welcome.optString("ambient", "off") != "off"
        if (daemonOpen == prefs.secondEar) return
        if (prefs.secondEar) {
            send(
                JSONObject().put("id", "ambient_control").put("submode", "passive")
                    .put("bystander_transcript", true)
            )
            return
        }
        // Обратный случай: буфер демона открыт, а слушать его некому — так
        // бывает после перезапуска приложения. Открытый буфер без микрофона
        // хуже закрытого: на вопрос «что он сказал» он ответит вчерашним
        // разговором и промолчит про сегодняшний.
        send(JSONObject().put("id", "ambient_control").put("submode", "off").put("wipe", true))
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
                        // На «не обработано» здесь полагаться нельзя: начиная
                        // с Android 5 система сама разбирает кнопку уже после
                        // этого колбэка, и false тут — обычное дело, а не
                        // поломка. Поэтому ждём: если через полсекунды окно
                        // так и не открылось, значит действительно мимо.
                        main.postDelayed({
                            if (System.currentTimeMillis() - buttonActedAt > 500) {
                                report(getString(R.string.headset_key_ignored, name))
                            }
                        }, 500)
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
        // Кнопку система умеет доставить дважды: своим путём и через
        // приёмник из манифеста. Два окна подряд — это две попытки
        // распознавания на один микрофон, то есть ERROR_CLIENT и потерянная
        // реплика. Второе нажатие за полсекунды — то же самое нажатие.
        val now = System.currentTimeMillis()
        // Отметку ставим и на отброшенном нажатии: иначе проверка второго
        // касания смотрит на время первого, видит «прошло больше полсекунды»
        // и печатает, что кнопка ни на что не назначена, — про нажатие,
        // которое только что сработало.
        val debounced = now - buttonActedAt < BUTTON_DEBOUNCE_MS
        buttonActedAt = now
        if (debounced) return
        if (speaking) silence()
        // Микрофон мог держать круг второго уха. Не отобрать его — значит
        // открыть окно, в которое никто не слушает: сказанное уехало бы в
        // буфер как чужая речь, а нажатие выглядело бы проигнорированным.
        stopRoom()
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
    /**
     * Приглушить чужой звук на время ответа.
     *
     * `MAY_DUCK`, а не полный захват: человек слушает музыку и не просил её
     * останавливать — он просил ответить. Отпускаем, как только замолчали.
     * Без этого ответ подмешивался в подкаст на полной громкости и был
     * неразборчив, а сигнал «слушаю» — единственное подтверждение, что
     * микрофон открылся, — не слышен вовсе.
     */
    private fun takeAudioFocus() {
        if (audioFocus) return
        audioFocus = runCatching {
            val audio = getSystemService(AudioManager::class.java)
            @Suppress("DEPRECATION")
            audio.requestAudioFocus(
                null, AudioManager.STREAM_MUSIC,
                AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK
            ) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
        }.getOrDefault(false)
    }

    private fun dropAudioFocus() {
        if (!audioFocus) return
        audioFocus = false
        runCatching {
            @Suppress("DEPRECATION")
            getSystemService(AudioManager::class.java).abandonAudioFocus(null)
        }
    }

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
        // Комната слушается на языке микрофона, пока ей не задали свой, — и
        // её распознаватель держит прежние extras до пересоздания.
        stopRoom()
        runCatching { roomEar?.destroy() }
        roomEar = null
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
        if (!onHeadphones()) {
            runCatching { wake?.stop() }
            // И второе ухо тоже: свой же ответ, услышанный из динамика,
            // лёг бы в буфер как чужая речь — и Claude пересказал бы его.
            // В наушниках этого пути нет, и комнату можно слушать дальше:
            // ответ звучит в ухе, разговор рядом продолжается.
            stopRoom()
        }

        val engine = when {
            speaks(tts, languageTag) -> tts
            spareReady -> if (speaks(spare, languageTag)) spare else null
            // Системный движок пробовали поднять и не подняли: ждать больше
            // нечего. Раньше сюда попадал КАЖДЫЙ следующий ответ — `pending`
            // переписывался, `ensureSpare` выходил сразу, потому что объект
            // уже есть, и реплика исчезала молча. Навсегда.
            spare != null -> null
            else -> {
                // Системный движок ещё не поднят: поднимаем и договариваем
                // эту же реплику, когда он будет готов.
                pending = text to languageTag
                ensureSpare()
                // Слушателя мы уже выключили, а речи не будет ещё секунду:
                // без сторожа это дыра в слышимости, и молчаливая.
                main.postDelayed(speechWatchdog, SPEECH_MARGIN_MS)
                return
            }
        }
        if (engine == null) {
            noVoiceFor(languageTag)
            return
        }
        runCatching {
            // Пока говорим — приглушить чужую музыку. Без этого ответ
            // подмешивался в подкаст на полной громкости и был неразборчив,
            // а сигнал «слушаю» — единственное подтверждение, что микрофон
            // открылся, — не слышен вовсе.
            takeAudioFocus()
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
        // Оборванный ответ не открывает окно для продолжения разговора:
        // `tts.stop()` зовёт `onDone`, а тот ставит окно на пятнадцать
        // секунд. В шумной комнате в это окно влетала чужая фраза — и
        // уезжала демону как сказанная хозяином.
        windowUntil = 0
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
        ready = false
        socket = http.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(
                    JSONObject().put("id", "hello").put("v", 1)
                        .put("token", prefs.token).put("device_id", "android")
                        // Демон отвечает на языке телефона, пока не услышит
                        // другой: иначе первая же реплика пришла бы по-английски.
                        .put("language", prefs.language)
                        // Закреплённый язык ответа переживает переподключение
                        // только так: отсутствие поля демон читает как
                        // «снять закрепление», а не как «не трогать».
                        .put("reply_language", prefs.replyLanguage)
                        .put("app_version", "0.4.0").toString()
                )
                main.post {
                    unauthorized = false
                    // Реплики можно отпускать только теперь: `newWebSocket`
                    // отдаёт сокет сразу, до рукопожатия, а окхттп копит
                    // отправленное и выливает его ПЕРЕД `onOpen`. Реплика,
                    // сказанная в эту щель, обгоняла hello — демон отвечал
                    // «сначала hello», ронял её и сообщал телефону, что токен
                    // не тот. Токен при этом был в порядке.
                    ready = true
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

            /**
             * Закрыл демон — а этого телефон не замечал по сорок секунд.
             *
             * okhttp зовёт `onClosed` только если закрылись обе стороны. Когда
             * закрывает демон и клиент не отвечает, приходит ровно этот
             * колбэк, и его не было. Всё это время сокет считался живым:
             * переподключения не назначалось, связь показывалась зелёной, а
             * `send` складывал реплики в сокет, который никто не читает, и
             * возвращал «отправлено». Человек говорил в пустоту и слышал
             * подтверждающий сигнал. Измерено: 39 секунд.
             */
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                runCatching { webSocket.close(1000, null) }
                main.post {
                    lost(if (unauthorized) LinkState.REFUSED else LinkState.NO_SERVER, reason, 0)
                }
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
        ready = false
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
        // Счётчик попыток не трогаем: человек говорит в каждую паузу, и
        // обнуление здесь роняло лестницу пауз с минуты обратно на две
        // секунды — час лежащего демона превращался в час стука в дверь.
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
            "welcome" -> {
                // Лестницу пауз сбрасывает `welcome`, а не открытый сокет:
                // демон принимает рукопожатие с любым токеном и только потом
                // отказывает. Сброс в `onOpen` означал, что неверный токен
                // долбится в дверь каждые две секунды — и жалуется при этом
                // на «сервер не отвечает» вместо «токен не тот».
                attempt = 0
                val problem = message.optString("credential_problem")
                report(
                    if (problem.isNotBlank()) getString(R.string.ready_credential, problem)
                    else getString(R.string.ready_credential, message.optString("credential"))
                )
                restoreEar(message)
            }
            // Демон говорит, что у него закрепилось на самом деле. Телефон
            // предлагает пять языков реплик, демон говорит на четырёх, и
            // «Клод, иврит» закреплялся только на трубке: она читала ответы
            // ивритским голосом, а приходили они по-русски.
            "language" -> {
                val pinned = message.optString("reply")
                val mine = prefs.replyLanguage
                if (pinned.isBlank() && mine.isNotBlank()) {
                    prefs.replyLanguage = ""
                    report(getString(R.string.language_not_supported, mine))
                }
            }
            "ambient_control" -> onAmbient(message)
            // Ветки не было: человек говорил «да», разрешение проходило, и
            // он об этом не узнавал ничем. Единственный его канал — звук.
            "permission_result" -> {
                val approved = message.optBoolean("approved")
                if (approved) signals?.accepted(route?.onBluetoothMic == true)
                else signals?.missed(route?.onBluetoothMic == true)
                report(getString(
                    if (approved) R.string.permission_allowed else R.string.permission_rejected))
            }
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

    /** Ушла ли реплика на самом деле. */
    private fun send(payload: JSONObject): Boolean {
        val ws = socket
        // `ready` вместо `ws != null`: сокет существует с первой миллисекунды
        // рукопожатия, а слушать его на той стороне ещё некому.
        if (ws == null || !ready || !ws.send(payload.toString())) {
            // Реплика пропала молча — это и есть «он меня не слышит».
            // Молча она пропадать и не должна: строчку в уведомлении человек
            // с телефоном в кармане не видит, и единственный канал, который
            // у него есть, — звук в ухе.
            signals?.missed(route?.onBluetoothMic == true)
            report(getString(R.string.not_sent, getString(linkState.label)))
            // Раз человек говорит, самое время попробовать связаться снова.
            main.post { connectNow() }
            return false
        }
        return true
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
            //
            // Открытое второе ухо стоит рядом и не исчезает ни на одной фазе:
            // телефон слушает других людей, и это должно быть видно всё время,
            // а не только в ту секунду, когда ухо открыли.
            .setSubText(
                if (prefs.secondEar) getString(R.string.second_ear_badge, getString(linkState.label))
                else getString(linkState.label)
            )
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
        // Служба уже мертва: её уведомление «слушаю» больше ни за чем не
        // стоит, снять его нельзя пальцем, и висит оно до перезагрузки.
        if (stopped) return
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

    /**
     * Служба умирает — и человек должен об этом узнать.
     *
     * Широковещание слушает только открытый экран. С телефоном в кармане оно
     * не доходит никуда, а уведомления на этом пути ещё не было вовсе: смерть
     * выглядела как полностью нормальный телефон. Человек узнавал о ней в
     * следующий раз, когда случайно открывал приложение.
     */
    private fun fail(reason: String) {
        Log.e(TAG, reason)
        runCatching { prefs.lastError = reason }
        sendBroadcast(Intent(ACTION_STATUS).setPackage(packageName).putExtra(EXTRA_TEXT, reason))
        runCatching {
            createChannel()
            val open = PendingIntent.getActivity(
                this, 0, Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            )
            getSystemService(NotificationManager::class.java).notify(
                FAILURE_ID,
                NotificationCompat.Builder(this, CHANNEL)
                    .setSmallIcon(R.drawable.ic_mic)
                    .setContentTitle(getString(R.string.service_died))
                    .setContentText(reason)
                    .setStyle(NotificationCompat.BigTextStyle().bigText(reason))
                    .setPriority(NotificationCompat.PRIORITY_HIGH)
                    .setOngoing(false)
                    .setAutoCancel(true)
                    .setContentIntent(open)
                    .build()
            )
        }
        stopSelf()
    }
}
