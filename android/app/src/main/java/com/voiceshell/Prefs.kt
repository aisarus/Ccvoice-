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

    /**
     * Язык реплик: en-US, ru-RU, es-ES, zh-CN, he-IL. Wake word всегда
     * слушается локально, независимо от этого выбора.
     *
     * По умолчанию — язык телефона, если он из списка: человек, поставивший
     * приложение, уже сказал системе, на каком языке говорит, и спрашивать
     * его об этом второй раз незачем.
     */
    var language: String
        get() = sp.getString("language", deviceLanguage()) ?: deviceLanguage()
        set(value) = sp.edit().putString("language", value).apply()

    /**
     * Язык ответа: пустая строка — «как спросили».
     *
     * Отдельно от языка распознавания нарочно. Распознаватель Android умеет
     * слушать ровно один язык за раз, и это его ограничение, а не выбор
     * человека. А вот на каком языке отвечать — выбор, и голосом меняется
     * именно он: «Клод, английский» переключает ответ, но не микрофон.
     */
    var replyLanguage: String
        get() = sp.getString("reply_language", "") ?: ""
        set(value) = sp.edit().putString("reply_language", value.trim()).apply()

    /**
     * Слушать несколько языков сразу (Android 13+).
     *
     * По умолчанию включено — таким и было намерение кода, который это уже
     * пытался включить. Выключается голосом: если на конкретном телефоне от
     * этого портится распознавание основного языка, спорить с ним незачем.
     */
    var multilingual: Boolean
        get() = sp.getBoolean("multilingual", true)
        set(value) = sp.edit().putBoolean("multilingual", value).apply()

    private fun deviceLanguage(): String {
        val want = java.util.Locale.getDefault().language.lowercase()
        return SUPPORTED.firstOrNull { it.startsWith(want) } ?: "en-US"
    }

    /** Последняя ошибка службы: без компьютера это единственный способ её увидеть. */
    var lastError: String
        get() = sp.getString("last_error", "") ?: ""
        set(value) = sp.edit().putString("last_error", value).apply()

    /** Ночной режим: отвечать текстом в лог, не озвучивая. */
    var mute: Boolean
        get() = sp.getBoolean("mute", false)
        set(value) = sp.edit().putBoolean("mute", value).apply()

    /**
     * Слушать микрофоном bluetooth-гарнитуры, а не телефона.
     *
     * По умолчанию включено: надел гарнитуру — говоришь в неё. Выключается,
     * если конкретная гарнитура ведёт себя плохо на своём канале связи.
     */
    var btMic: Boolean
        get() = sp.getBoolean("bt_mic", true)
        set(value) = sp.edit().putBoolean("bt_mic", value).apply()

    /**
     * Что делать со знаками ударения: auto | plus | acute | off.
     *
     * Авто: RHVoice получает знак «+» как есть, остальные движки — чистый
     * текст, иначе плюс прозвучит вслух.
     */
    var stressStyle: String
        get() = sp.getString("stress_style", Stress.AUTO) ?: Stress.AUTO
        set(value) = sp.edit().putString("stress_style", value).apply()

    /** Движок синтеза (пакет приложения); пусто — системный по умолчанию. */
    var engine: String
        get() = sp.getString("engine", "") ?: ""
        set(value) = sp.edit().putString("engine", value).apply()

    /** Выбранный голос синтеза; пусто — берём лучший по эвристике. */
    var voice: String
        get() = sp.getString("voice", "") ?: ""
        set(value) = sp.edit().putString("voice", value).apply()

    /**
     * Мерить ли громкость реплики и слать признаки демону.
     *
     * Выключатель нужен не для красоты: если на конкретной трубке шкала RMS
     * ведёт себя странно, демон начнёт принимать хозяина за соседа и молча
     * ронять команды. Тогда одно нажатие возвращает прежнее поведение —
     * реплика уходит без признаков, с ролью-подсказкой.
     */
    var acoustics: Boolean
        get() = sp.getBoolean("acoustics", true)
        set(value) = sp.edit().putBoolean("acoustics", value).apply()

    /**
     * Норма громкости для конкретного микрофона.
     *
     * Каждый вход слышит по-своему, и общая норма для телефона и гарнитуры
     * означала бы, что после надевания гарнитуры хозяин выглядит чужим.
     */
    fun baseline(device: String): Baseline? {
        val speech = sp.getFloat("base_speech_$device", Float.NaN)
        val snr = sp.getFloat("base_snr_$device", Float.NaN)
        if (speech.isNaN() || snr.isNaN()) return null
        return Baseline(speech.toDouble(), snr.toDouble())
    }

    fun setBaseline(device: String, value: Baseline) {
        sp.edit()
            .putFloat("base_speech_$device", value.speech.toFloat())
            .putFloat("base_snr_$device", value.snr.toFloat())
            .apply()
    }

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

    companion object {
        /** Порядок тот же, что у подписей в `reply_language_names`. */
        val SUPPORTED = listOf("en-US", "ru-RU", "es-ES", "zh-CN", "he-IL")
    }
}
