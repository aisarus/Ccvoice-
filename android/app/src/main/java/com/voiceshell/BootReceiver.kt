package com.voiceshell

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat

/**
 * После перезагрузки телефона служба должна вернуться сама.
 *
 * И если не вернулась — об этом должен остаться след. Раньше исключение
 * глоталось молча: человек перезагружал телефон на ночь, а утром оболочки
 * просто не было, и поле последней ошибки хранило что-то позавчерашнее,
 * потому что `onCreate` до записи в него не доходил вовсе.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        // Обновление приложения тоже гасит службу, и само она не возвращалась.
        if (intent.action !in ACTIONS) return
        val prefs = Prefs(context)
        if (!prefs.isConfigured) return
        // Разрешение на микрофон Android умеет отзывать у неиспользуемых
        // приложений сам. Без проверки служба поднималась, падала на первой
        // же строчке и умирала тише, чем если бы не поднималась вовсе.
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            prefs.lastError = context.getString(R.string.error_mic_permission)
            return
        }
        runCatching {
            ContextCompat.startForegroundService(context, Intent(context, VoiceService::class.java))
        }.onFailure {
            // Android 15 запрещает поднимать службу с микрофоном по загрузке.
            // Что бы это ни было — человек должен увидеть причину, а не пустой
            // экран с работающим на вид телефоном.
            prefs.lastError = "boot: ${it.javaClass.simpleName}: ${it.message}"
        }
    }

    private companion object {
        val ACTIONS = setOf(
            Intent.ACTION_BOOT_COMPLETED,
            "android.intent.action.LOCKED_BOOT_COMPLETED",
            Intent.ACTION_MY_PACKAGE_REPLACED,
        )
    }
}
