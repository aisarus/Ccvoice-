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
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

/** Ввод один раз: адрес и токен. Дальше всё живёт в фоновой службе. */
class MainActivity : AppCompatActivity() {

    private lateinit var prefs: Prefs
    private lateinit var status: TextView

    private val lines = ArrayDeque<String>()
    private var authUrl: String? = null
    private var voices: List<String> = emptyList()

    private val statusReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            intent?.getStringArrayListExtra(VoiceService.EXTRA_VOICES)?.let { names ->
                voices = names
                val spinner = findViewById<Spinner>(R.id.voice)
                spinner.adapter = ArrayAdapter(
                    this@MainActivity, android.R.layout.simple_spinner_dropdown_item,
                    if (names.isEmpty()) listOf("голосов не нашлось") else names
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

        val codes = listOf("ru-RU", "en-US", "he-IL")
        val labels = listOf("русский", "English", "עברית")
        language.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, labels)
        language.setSelection(codes.indexOf(prefs.language).coerceAtLeast(0))

        findViewById<Button>(R.id.start).setOnClickListener {
            prefs.server = server.text.toString()
            prefs.token = token.text.toString()
            prefs.language = codes[language.selectedItemPosition]
            if (!prefs.isConfigured) {
                status.text = "нужны адрес сервиса и токен"
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
            status.text = "отправлено: $text"
        }

        val mute = findViewById<Button>(R.id.mute)
        fun paintMute() { mute.text = if (prefs.mute) "озвучка: выкл" else "озвучка: вкл" }
        paintMute()
        mute.setOnClickListener { prefs.mute = !prefs.mute; paintMute() }

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
            status.text = "голос: $name"
        }

        findViewById<Button>(R.id.battery).setOnClickListener { askForBackgroundFreedom() }

        // Токен уже есть — вставить и сохранить, без OAuth-хождений.
        findViewById<Button>(R.id.authSave).setOnClickListener {
            val token = findViewById<EditText>(R.id.authToken).text.toString().trim()
            if (token.isEmpty()) {
                status.text = "вставь токен, который начинается с sk-ant-"
                return@setOnClickListener
            }
            startService(
                Intent(this, VoiceService::class.java)
                    .setAction(VoiceService.ACTION_AUTH_SET)
                    .putExtra(VoiceService.EXTRA_CODE, token)
            )
            status.text = "отправил токен на сервер"
        }

        // Подключение подписки Claude — тот же флоу, что в веб-клиенте.
        findViewById<Button>(R.id.authStart).setOnClickListener {
            startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_AUTH_START))
            status.text = "запрашиваю ссылку…"
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
            status.text = "ошибка скопирована"
        }

        findViewById<Button>(R.id.stop).setOnClickListener {
            stopService(Intent(this, VoiceService::class.java))
            status.text = "служба остановлена"
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        val micIndex = permissions.indexOf(Manifest.permission.RECORD_AUDIO)
        val micGranted = micIndex < 0 ||
            grantResults.getOrNull(micIndex) == PackageManager.PERMISSION_GRANTED
        if (micGranted) launchService() else status.text = "без микрофона работать не смогу"
    }

    /** Без этого Samsung усыпляет службу через несколько минут после выключения экрана. */
    private fun askForBackgroundFreedom() {
        val power = getSystemService(PowerManager::class.java)
        if (power.isIgnoringBatteryOptimizations(packageName)) {
            status.text = "фоновая работа уже разрешена"
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
        status.text = "служба запускается…"
    }

    override fun onStart() {
        super.onStart()
        if (prefs.lastError.isNotBlank()) status.text = "прошлый запуск: ${prefs.lastError}"
        val filter = IntentFilter(VoiceService.ACTION_STATUS)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(statusReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(statusReceiver, filter)
        }
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
