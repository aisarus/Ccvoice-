package com.voiceshell

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.view.Gravity
import android.view.ViewGroup
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/** Ввод один раз: адрес и токен. Дальше всё живёт в фоновой службе. */
class MainActivity : AppCompatActivity() {

    private lateinit var prefs: Prefs
    private lateinit var status: TextView

    private val lines = ArrayDeque<String>()
    private var authUrl: String? = null
    private var voices: List<String> = emptyList()
    private var engines: List<Pair<String, String>> = emptyList()   // подпись -> пакет
    /** Пока служба молчит, состояние связи неизвестно — так и говорим. */
    private var link = LinkState.UNKNOWN

    private val statusReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            intent?.getStringExtra(VoiceService.EXTRA_LINK)?.let { name ->
                link = runCatching { LinkState.valueOf(name) }.getOrDefault(LinkState.UNKNOWN)
                paintReadiness()
            }
            intent?.getStringArrayListExtra(VoiceService.EXTRA_ENGINES)?.let { raw ->
                engines = raw.map { it.split('\u0000').let { parts -> parts[0] to parts.getOrElse(1) { "" } } }
                val spinner = findViewById<Spinner>(R.id.engine)
                spinner.adapter = ArrayAdapter(
                    this@MainActivity, android.R.layout.simple_spinner_dropdown_item,
                    if (engines.isEmpty()) listOf(getString(R.string.engines_none))
                    else engines.map { it.first }
                )
                val current = engines.indexOfFirst { it.second == prefs.engine }
                if (current >= 0) spinner.setSelection(current)
            }
            intent?.getStringArrayListExtra(VoiceService.EXTRA_VOICES)?.let { names ->
                voices = names
                val spinner = findViewById<Spinner>(R.id.voice)
                spinner.adapter = ArrayAdapter(
                    this@MainActivity, android.R.layout.simple_spinner_dropdown_item,
                    if (names.isEmpty()) listOf(getString(R.string.voices_none)) else names
                )
                val saved = voices.indexOf(prefs.voice)
                if (saved >= 0) spinner.setSelection(saved)
            }
            intent?.getStringExtra(VoiceService.EXTRA_AUTH_URL)?.let { url ->
                authUrl = url
                findViewById<Button>(R.id.authOpen).isEnabled = true
                findViewById<EditText>(R.id.authCode).visibility = android.view.View.VISIBLE
                findViewById<Button>(R.id.authSend).visibility = android.view.View.VISIBLE
            }
            intent?.getStringExtra(VoiceService.EXTRA_AUTH_TOKEN)?.let { token ->
                findViewById<EditText>(R.id.authCode).setText(token)
            }
            val text = intent?.getStringExtra(VoiceService.EXTRA_TEXT).orEmpty()
            if (text.isBlank()) return
            status.text = text
            lines.addFirst(text)
            while (lines.size > 20) lines.removeLast()
            findViewById<TextView>(R.id.log).text = lines.joinToString("\n")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = Prefs(this)

        val server = findViewById<EditText>(R.id.server)
        val token = findViewById<EditText>(R.id.token)
        val language = findViewById<Spinner>(R.id.language)
        status = findViewById(R.id.status)
        server.setText(prefs.server)
        token.setText(prefs.token)

        // Коды — для распознавателя и синтеза, подписи — для человека: они
        // названы на самих себе и от языка интерфейса не зависят.
        val codes = listOf("en-US", "ru-RU", "es-ES", "zh-CN", "he-IL")
        language.adapter = ArrayAdapter(
            this, android.R.layout.simple_spinner_dropdown_item,
            resources.getStringArray(R.array.reply_language_names)
        )
        language.setSelection(codes.indexOf(prefs.language).coerceAtLeast(0))

        findViewById<Button>(R.id.start).setOnClickListener {
            prefs.server = server.text.toString()
            prefs.token = token.text.toString()
            prefs.language = codes[language.selectedItemPosition]
            if (!prefs.isConfigured) {
                status.text = getString(R.string.error_need_server_and_token)
                return@setOnClickListener
            }
            // Сначала разрешение, потом служба: без микрофона она падает при старте.
            if (missingPermissions().isEmpty()) launchService() else requestPermissions()
        }

        // Реплика текстом: проверить всё, кроме микрофона, не издав ни звука.
        val say = findViewById<EditText>(R.id.say)
        findViewById<Button>(R.id.saySend).setOnClickListener {
            val text = say.text.toString().trim()
            if (text.isEmpty()) return@setOnClickListener
            startService(
                Intent(this, VoiceService::class.java)
                    .setAction(VoiceService.ACTION_SAY)
                    .putExtra(VoiceService.EXTRA_TEXT, text)
            )
            say.setText("")
            status.text = getString(R.string.sent_text, text)
        }

        val mute = findViewById<Button>(R.id.mute)
        fun paintMute() { mute.setText(if (prefs.mute) R.string.speech_off else R.string.speech_on) }
        paintMute()
        mute.setOnClickListener { prefs.mute = !prefs.mute; paintMute() }

        // Ударения: если конкретная сборка RHVoice не понимает «+», здесь
        // переключается запись — проверяется на слух полем «скажи».
        val stress = findViewById<Button>(R.id.stress)
        fun paintStress() { stress.setText(Stress.label(prefs.stressStyle)) }
        paintStress()
        stress.setOnClickListener {
            prefs.stressStyle = Stress.next(prefs.stressStyle)
            paintStress()
        }

        // Микрофон гарнитуры: служба поднимает канал связи, здесь только выбор.
        val btmic = findViewById<Button>(R.id.btmic)
        fun paintMic() { btmic.setText(if (prefs.btMic) R.string.mic_headset else R.string.mic_phone) }
        paintMic()
        btmic.setOnClickListener {
            prefs.btMic = !prefs.btMic
            paintMic()
            startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_MIC))
        }

        // Движок синтеза: RHVoice ставится отдельным приложением и появляется здесь.
        findViewById<Button>(R.id.engineList).setOnClickListener {
            startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_ENGINES))
        }
        findViewById<Button>(R.id.engineUse).setOnClickListener {
            val spinner = findViewById<Spinner>(R.id.engine)
            val engine = engines.getOrNull(spinner.selectedItemPosition) ?: return@setOnClickListener
            startService(
                Intent(this, VoiceService::class.java)
                    .setAction(VoiceService.ACTION_SET_ENGINE)
                    .putExtra(VoiceService.EXTRA_CODE, engine.second)
            )
            status.text = getString(R.string.engine_selected, engine.first)
        }
        findViewById<Button>(R.id.engineInstall).setOnClickListener { installVoiceEngine() }

        // Голос синтеза: список того, что есть в системе, с примером на слух.
        findViewById<Button>(R.id.voiceList).setOnClickListener {
            startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_VOICES))
        }
        findViewById<Button>(R.id.voiceTry).setOnClickListener {
            val spinner = findViewById<Spinner>(R.id.voice)
            val name = voices.getOrNull(spinner.selectedItemPosition) ?: return@setOnClickListener
            prefs.voice = name
            startService(
                Intent(this, VoiceService::class.java)
                    .setAction(VoiceService.ACTION_TRY_VOICE)
                    .putExtra(VoiceService.EXTRA_CODE, name)
            )
            status.text = getString(R.string.voice_selected, name)
        }

        findViewById<Button>(R.id.battery).setOnClickListener { askForBackgroundFreedom() }

        // Признаки говорящего: если на этой трубке шкала громкости врёт,
        // выключатель возвращает прежнее поведение одним нажатием.
        val acoustics = findViewById<Button>(R.id.acoustics)
        fun paintAcoustics() {
            acoustics.setText(
                if (prefs.acoustics) R.string.acoustics_on else R.string.acoustics_off
            )
        }
        paintAcoustics()
        acoustics.setOnClickListener {
            prefs.acoustics = !prefs.acoustics
            paintAcoustics()
            startService(
                Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_ACOUSTICS)
            )
        }

        // Токен уже есть — вставить и сохранить, без OAuth-хождений.
        findViewById<Button>(R.id.authSave).setOnClickListener {
            val token = findViewById<EditText>(R.id.authToken).text.toString().trim()
            if (token.isEmpty()) {
                status.text = getString(R.string.error_paste_token)
                return@setOnClickListener
            }
            startService(
                Intent(this, VoiceService::class.java)
                    .setAction(VoiceService.ACTION_AUTH_SET)
                    .putExtra(VoiceService.EXTRA_CODE, token)
            )
            status.text = getString(R.string.auth_token_sent)
        }

        // Подключение подписки Claude — тот же флоу, что в веб-клиенте.
        findViewById<Button>(R.id.authStart).setOnClickListener {
            startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_AUTH_START))
            status.text = getString(R.string.auth_requesting_link)
        }
        findViewById<Button>(R.id.authOpen).setOnClickListener {
            authUrl?.let { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(it))) }
        }
        findViewById<Button>(R.id.authSend).setOnClickListener {
            val code = findViewById<EditText>(R.id.authCode).text.toString()
            startService(
                Intent(this, VoiceService::class.java)
                    .setAction(VoiceService.ACTION_AUTH_CODE)
                    .putExtra(VoiceService.EXTRA_CODE, code)
            )
        }

        findViewById<Button>(R.id.copyError).setOnClickListener {
            val clipboard = getSystemService(android.content.ClipboardManager::class.java)
            clipboard.setPrimaryClip(
                android.content.ClipData.newPlainText("voice-shell", prefs.lastError)
            )
            // Прочитанную ошибку чистит тот, кто её забрал: служба этого
            // больше не делает, иначе ночное падение стиралось бы своим же
            // перезапуском раньше, чем человек проснётся.
            prefs.lastError = ""
            status.text = getString(R.string.error_copied)
        }

        findViewById<Button>(R.id.stop).setOnClickListener {
            stopService(Intent(this, VoiceService::class.java))
            link = LinkState.UNKNOWN
            status.text = getString(R.string.service_stopped)
            paintReadiness()
        }

        paintReadiness()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        paintReadiness()
        // Запрос из строки готовности — просто перерисовать; службу оттуда
        // поднимать рано, у неё своя строка и своя кнопка.
        if (requestCode != 1) return
        val micIndex = permissions.indexOf(Manifest.permission.RECORD_AUDIO)
        val micGranted = micIndex < 0 ||
            grantResults.getOrNull(micIndex) == PackageManager.PERMISSION_GRANTED
        if (micGranted) launchService() else status.text = getString(R.string.error_no_microphone)
    }

    /**
     * Проверка готовности: пять строк вместо гадания.
     *
     * Разрешение выдано, но служба спит; уведомления выключены, и система
     * гасит службу; адрес верный, но демон не запущен — снаружи всё это
     * выглядит одинаково «не работает». Строки перерисовываются при каждом
     * возврате на экран и при каждой смене состояния связи.
     */
    private fun paintReadiness() {
        val box = findViewById<LinearLayout>(R.id.readiness) ?: return
        val checks = Readiness.checks(
            mic = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED,
            notifications = runCatching {
                NotificationManagerCompat.from(this).areNotificationsEnabled()
            }.getOrDefault(true),
            background = runCatching {
                getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)
            }.getOrDefault(true),
            engine = prefs.engine,
            engines = ttsEngines(),
            link = if (prefs.isConfigured) link else LinkState.OFF,
        )
        findViewById<TextView>(R.id.ready).text = Readiness.summary(this, checks)
        box.removeAllViews()
        for (check in checks) {
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            row.addView(TextView(this).apply {
                text = Readiness.line(this@MainActivity, check)
                textSize = 13f
                layoutParams = LinearLayout.LayoutParams(
                    0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f
                )
            })
            val label = check.fix
            if (label != null) {
                row.addView(Button(this).apply {
                    setText(label)
                    textSize = 12f
                    setOnClickListener { fix(check.id) }
                })
            }
            box.addView(row, LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            ))
        }
    }

    /** Что стоит в системе и умеет говорить. */
    private fun ttsEngines(): List<String> = runCatching {
        packageManager
            .queryIntentServices(Intent(TextToSpeech.Engine.INTENT_ACTION_TTS_SERVICE), 0)
            .map { it.serviceInfo.packageName }
    }.getOrDefault(emptyList())

    /** Кнопка напротив строки готовности: одно нажатие — один шаг починки. */
    private fun fix(id: String) {
        when (id) {
            Readiness.MIC -> requestPermissions()
            Readiness.NOTIFY -> askForNotifications()
            Readiness.BACKGROUND -> askForBackgroundFreedom()
            // Ставить нечего — сначала магазин; есть из чего выбрать — список.
            Readiness.VOICE -> if (ttsEngines().isEmpty()) installVoiceEngine() else {
                startService(
                    Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_ENGINES)
                )
                status.text = getString(R.string.engines_listed_below)
            }
            Readiness.LINK -> when (link) {
                // Сеть не чинится из приложения — открываем то место, где чинится.
                LinkState.NO_NETWORK -> runCatching {
                    startActivity(Intent(Settings.ACTION_WIRELESS_SETTINGS))
                }
                LinkState.UNKNOWN -> launchService()
                else -> {
                    startService(
                        Intent(this, VoiceService::class.java)
                            .setAction(VoiceService.ACTION_RECONNECT)
                    )
                    status.text = getString(R.string.link_retrying)
                }
            }
        }
    }

    /** RHVoice ставится отдельным приложением; без магазина — ссылкой в браузер. */
    private fun installVoiceEngine() {
        runCatching {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("market://search?q=RHVoice")))
        }.onFailure {
            runCatching {
                startActivity(
                    Intent(Intent.ACTION_VIEW,
                        Uri.parse("https://play.google.com/store/search?q=RHVoice"))
                )
            }
        }
    }

    /**
     * Уведомления выключают не только запретом разрешения: на Android 12 и
     * старше их гасят в настройках приложения, и спросить оттуда нечего.
     */
    private fun askForNotifications() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 2
            )
            return
        }
        runCatching {
            startActivity(
                Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                    .setData(Uri.parse("package:$packageName"))
            )
        }
    }

    /** Без этого Samsung усыпляет службу через несколько минут после выключения экрана. */
    private fun askForBackgroundFreedom() {
        val power = getSystemService(PowerManager::class.java)
        if (power.isIgnoringBatteryOptimizations(packageName)) {
            status.text = getString(R.string.background_already_allowed)
            return
        }
        runCatching {
            startActivity(
                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                    .setData(Uri.parse("package:$packageName"))
            )
        }.onFailure {
            runCatching { startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) }
        }
    }

    private fun launchService() {
        ContextCompat.startForegroundService(this, Intent(this, VoiceService::class.java))
        status.text = getString(R.string.service_starting)
    }

    override fun onStart() {
        super.onStart()
        if (prefs.lastError.isNotBlank()) status.text = getString(R.string.last_error, prefs.lastError)
        val filter = IntentFilter(VoiceService.ACTION_STATUS)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(statusReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(statusReceiver, filter)
        }
        // Вернулись из системных настроек — то, что там разрешили, должно быть
        // видно сразу, а не после перезапуска приложения.
        paintReadiness()
    }

    override fun onStop() {
        super.onStop()
        runCatching { unregisterReceiver(statusReceiver) }
    }

    private fun missingPermissions(): List<String> {
        val wanted = mutableListOf<String>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            wanted += Manifest.permission.RECORD_AUDIO
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            wanted += Manifest.permission.POST_NOTIFICATIONS
        }
        return wanted
    }

    private fun requestPermissions() {
        val wanted = missingPermissions()
        if (wanted.isEmpty()) launchService()
        else ActivityCompat.requestPermissions(this, wanted.toTypedArray(), 1)
    }
}
