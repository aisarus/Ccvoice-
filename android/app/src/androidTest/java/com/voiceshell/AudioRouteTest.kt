package com.voiceshell

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioManager
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.rule.GrantPermissionRule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Проверки на живом Android.
 *
 * Маршрут микрофона трогает системный звук, и ошибка в нём видна только на
 * устройстве — из JVM-теста системного AudioManager не существует. Гарнитуры
 * у эмулятора нет, поэтому проверяем ровно то, что обязано происходить без
 * неё: слушаем телефоном, ничего не перезапускаем и не оставляем систему
 * в режиме разговора.
 */
@RunWith(AndroidJUnit4::class)
class AudioRouteTest {

    @get:Rule
    val permissions: GrantPermissionRule = GrantPermissionRule.grant(
        Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS
    )

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val audio = context.getSystemService(AudioManager::class.java)

    private fun route(changes: MutableList<String> = mutableListOf()) =
        AudioRoute(context, onChanged = { why -> changes += why }, log = {})

    @Test
    fun withoutAHeadsetItListensWithThePhone() {
        val changes = mutableListOf<String>()
        val route = route(changes)
        try {
            route.start(true)
            assertFalse("гарнитуры нет, а канал считается поднятым", route.onBluetoothMic)
            // Слова зависят от языка телефона, поэтому сверяемся с ресурсом, а
            // не с русской строкой: на эмуляторе система говорит по-английски.
            assertEquals(context.getString(R.string.mic_phone), route.describe())
            assertTrue("маршрут не менялся — перезапускать поток незачем", changes.isEmpty())
        } finally {
            route.release()
        }
    }

    @Test
    fun switchedOffItSaysWhyItListensWithThePhone() {
        val route = route()
        try {
            route.start(false)
            assertEquals(context.getString(R.string.mic_phone_bluetooth_off), route.describe())
        } finally {
            route.release()
        }
    }

    @Test
    fun itLeavesTheSystemAudioModeAsItFoundIt() {
        val before = audio.mode
        val route = route()
        route.start(true)
        route.release()
        assertEquals("телефон остался в режиме разговора", before, audio.mode)
    }

    /**
     * Службу из оболочки не запустить — она не exported. Отсюда можно: тест
     * идёт в процессе самого приложения. Проверяем то, что уже однажды
     * ломалось вживую: служба поднимается и говорит, чем слушает.
     */
    @Test
    fun theServiceStartsAndSaysWhatItListensWith() {
        val said = CountDownLatch(1)
        var line = ""
        // Без гарнитуры служба обязана сказать ровно это — на языке телефона.
        val expected = context.getString(R.string.mic_phone)
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                val text = intent?.getStringExtra(VoiceService.EXTRA_TEXT).orEmpty()
                if (text == expected) {
                    line = text
                    said.countDown()
                }
            }
        }
        context.registerReceiver(
            receiver, IntentFilter(VoiceService.ACTION_STATUS), Context.RECEIVER_NOT_EXPORTED
        )
        try {
            context.startForegroundService(Intent(context, VoiceService::class.java))
            assertTrue("служба не сказала, чем слушает", said.await(20, TimeUnit.SECONDS))
            assertEquals(expected, line)
        } finally {
            context.stopService(Intent(context, VoiceService::class.java))
            runCatching { context.unregisterReceiver(receiver) }
        }
    }
}
