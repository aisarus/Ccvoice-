package com.voiceshell

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Build
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

    private val statusReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            status.text = intent?.getStringExtra(VoiceService.EXTRA_TEXT) ?: ""
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
            requestPermissions()
            ContextCompat.startForegroundService(this, Intent(this, VoiceService::class.java))
            status.text = "служба запущена"
        }

        findViewById<Button>(R.id.stop).setOnClickListener {
            stopService(Intent(this, VoiceService::class.java))
            status.text = "служба остановлена"
        }
    }

    override fun onStart() {
        super.onStart()
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

    private fun requestPermissions() {
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
        if (wanted.isNotEmpty()) ActivityCompat.requestPermissions(this, wanted.toTypedArray(), 1)
    }
}
