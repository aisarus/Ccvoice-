package com.voiceshell

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat

/** После перезагрузки телефона служба должна вернуться сама. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        if (!Prefs(context).isConfigured) return
        runCatching {
            ContextCompat.startForegroundService(context, Intent(context, VoiceService::class.java))
        }
    }
}
