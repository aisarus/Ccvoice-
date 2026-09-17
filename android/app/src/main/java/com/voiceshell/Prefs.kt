package com.voiceshell

import android.content.Context

/** Ввод один раз: адрес сервиса и токен доступа. */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("voice-shell", Context.MODE_PRIVATE)

    var server: String
        get() = sp.getString("server", "") ?: ""
        set(value) = sp.edit().putString("server", value.trim()).apply()

    var token: String
        get() = sp.getString("token", "") ?: ""
        set(value) = sp.edit().putString("token", clean(value)).apply()

    /** Язык реплик: ru-RU, en-US, he-IL. Wake word всегда слушается локально. */
    var language: String
        get() = sp.getString("language", "ru-RU") ?: "ru-RU"
        set(value) = sp.edit().putString("language", value).apply()

    /** Последняя ошибка службы: без компьютера это единственный способ её увидеть. */
    var lastError: String
        get() = sp.getString("last_error", "") ?: ""
        set(value) = sp.edit().putString("last_error", value).apply()

    /** Ночной режим: отвечать текстом в лог, не озвучивая. */
    var mute: Boolean
        get() = sp.getBoolean("mute", false)
        set(value) = sp.edit().putBoolean("mute", value).apply()

    /** Движок синтеза (пакет приложения); пусто — системный по умолчанию. */
    var engine: String
        get() = sp.getString("engine", "") ?: ""
        set(value) = sp.edit().putString("engine", value).apply()

    /** Выбранный голос синтеза; пусто — берём лучший по эвристике. */
    var voice: String
        get() = sp.getString("voice", "") ?: ""
        set(value) = sp.edit().putString("voice", value).apply()

    val isConfigured: Boolean get() = server.isNotBlank() && token.isNotBlank()

    /**
     * Токен обычно копируют из вывода `grep VOICE_TOKEN /etc/voice-shell.env`,
     * вместе с именем переменной, знаком равенства и кавычками. Чистим сами,
     * а не заставляем человека вглядываться в строку.
     */
    private fun clean(raw: String): String = raw.trim()
        .removePrefix("VOICE_TOKEN")
        .trimStart('=', ' ')
        .trim('"', '\'', ' ')
        .substringBefore(' ')

    /** https://host -> wss://host, http://host -> ws://host. */
    fun socketUrl(): String {
        val raw = server.trim().removeSuffix("/")
        return when {
            raw.startsWith("https://") -> "wss://" + raw.removePrefix("https://")
            raw.startsWith("http://") -> "ws://" + raw.removePrefix("http://")
            raw.startsWith("wss://") || raw.startsWith("ws://") -> raw
            else -> "wss://$raw"
        }
    }
}
