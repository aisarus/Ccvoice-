package com.voiceshell

/**
 * Разбор реплики на телефоне: обращение и «стоп».
 *
 * «Стоп» распознаётся всегда — до обращения, во время ответа и во время работы,
 * потому что остановка не должна зависеть от круга по сети.
 */
object Intents {
    val WAKE = listOf("клод", "клода", "клоуд", "клауд", "claude", "клот", "клод")
    private val STOP_WORK = listOf(
        "стоп работу", "стоп работа", "останови работу", "останови", "прекрати", "отмени", "отмена",
        "stop working", "stop the work", "abort", "cancel"
    )
    private val STOP_VOICE = listOf(
        "стоп", "тихо", "хватит", "замолчи",
        "stop", "quiet", "enough", "shut up"
    )

    fun normalise(text: String): String =
        text.lowercase().replace(Regex("[^\\p{L}\\p{N}\\s]"), " ").replace(Regex("\\s+"), " ").trim()

    fun hasWake(text: String): Boolean {
        val first = normalise(text).substringBefore(' ')
        return WAKE.any { it == first }
    }

    fun stripWake(text: String): String {
        val normalised = normalise(text)
        val first = normalised.substringBefore(' ')
        if (!WAKE.any { it == first }) return text.trim()
        return normalised.substringAfter(' ', "").trim().ifBlank { text.trim() }
    }

    /** Смена языка голосом: «Клод, английский». Возвращает код языка или null. */
    private val LANGUAGES = mapOf(
        "ru-RU" to listOf("русский", "по русски", "russian", "рашн"),
        "en-US" to listOf("английский", "по английски", "english", "инглиш"),
        "he-IL" to listOf("иврит", "на иврите", "hebrew", "עברית")
    )

    fun languageSwitch(text: String): String? {
        val bare = stripWake(normalise(text))
            .removePrefix("переключись на ").removePrefix("переключись ")
            .removePrefix("говори ").removePrefix("switch to ").removePrefix("speak ").trim()
        for ((code, words) in LANGUAGES) {
            if (words.any { bare == it || bare.startsWith("$it ") }) return code
        }
        return null
    }

    /** Язык ответа определяется по письменности, а не по настройке. */
    fun scriptLanguage(text: String, fallback: String): String = when {
        text.any { it in '\u0590'..'\u05FF' } -> "he-IL"
        text.any { it in '\u0400'..'\u04FF' } -> "ru-RU"
        text.any { it in 'a'..'z' || it in 'A'..'Z' } -> "en-US"
        else -> fallback
    }

    /** "work", "voice" или null. */
    fun stopIntent(text: String): String? {
        val bare = stripWake(normalise(text))
        STOP_WORK.firstOrNull { bare == it || bare.startsWith("$it ") }?.let { return "work" }
        STOP_VOICE.firstOrNull { bare == it || bare.startsWith("$it ") }?.let { return "voice" }
        return null
    }
}
