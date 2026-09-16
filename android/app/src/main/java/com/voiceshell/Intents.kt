package com.voiceshell

/**
 * Разбор реплики на телефоне: обращение и «стоп».
 *
 * «Стоп» распознаётся всегда — до обращения, во время ответа и во время работы,
 * потому что остановка не должна зависеть от круга по сети.
 */
object Intents {
    val WAKE = listOf("клод", "клода", "клоуд", "клауд", "claude")
    private val STOP_WORK = listOf(
        "стоп работу", "стоп работа", "останови работу", "останови", "прекрати", "отмени", "отмена"
    )
    private val STOP_VOICE = listOf("стоп", "тихо", "хватит", "замолчи")

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

    /** "work", "voice" или null. */
    fun stopIntent(text: String): String? {
        val bare = stripWake(normalise(text))
        STOP_WORK.firstOrNull { bare == it || bare.startsWith("$it ") }?.let { return "work" }
        STOP_VOICE.firstOrNull { bare == it || bare.startsWith("$it ") }?.let { return "voice" }
        return null
    }
}
