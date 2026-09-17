package com.voiceshell

import android.app.Application
import android.util.Log
import java.io.PrintWriter
import java.io.StringWriter

/**
 * Перехватывает падения и сохраняет их.
 *
 * Без компьютера логи посмотреть негде, поэтому причина должна пережить
 * перезапуск и показаться в самом приложении.
 */
class VoiceApp : Application() {

    override fun onCreate() {
        super.onCreate()
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            runCatching {
                val trace = StringWriter().also { error.printStackTrace(PrintWriter(it)) }.toString()
                Prefs(this).lastError = "${error.javaClass.name}: ${error.message}\n" +
                    trace.lineSequence().take(12).joinToString("\n")
                Log.e("VoiceShell", "падение в ${thread.name}", error)
            }
            previous?.uncaughtException(thread, error)
        }
    }
}
