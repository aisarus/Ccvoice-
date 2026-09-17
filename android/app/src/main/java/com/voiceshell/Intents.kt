package com.voiceshell

/**
 * Разбор реплики на телефоне: обращение и «стоп».
 *
 * «Стоп» распознаётся всегда — до обращения, во время ответа и во время работы,
 * потому что остановка не должна зависеть от круга по сети.
 */
object Intents {
    val WAKE = listOf("клод", "клода", "клоуд", "клауд", "claude", "клот", "клоуде")
    /**
     * От чего считаем допуск в одну букву.
     *
     * Только настоящие формы обращения. Мерить от «клот» — самого по себе
     * искажения — значит принимать за обращение всё в двух буквах от «Клод»:
     * так «крот» становится вызовом.
     */
    private val WAKE_ROOTS = listOf("клод", "клоуд", "клауд", "claude")
    /** Слова, которыми начинают команду: их нельзя принимать за обращение. */
    private val NOT_WAKE = setOf("код", "чат", "кот", "что", "как", "код?")
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

    fun hasWake(text: String): Boolean = isWake(normalise(text).substringBefore(' '))

    /**
     * Обращение с допуском в одну букву.
     *
     * Маленькая модель слышит «клот», «клад», «плод» вместо «Клод» — и это
     * ровно то, из-за чего он «отзывается через раз». Допуск берём только для
     * слов такой же длины: «код» и «чат» — начала команд, а не обращение.
     */
    fun isWake(word: String): Boolean {
        if (word.isBlank() || word in NOT_WAKE) return false
        if (WAKE.any { it == word }) return true
        if (word.length < 4) return false
        return WAKE_ROOTS.any { withinOneEdit(it, word) }
    }

    private fun withinOneEdit(a: String, b: String): Boolean {
        if (kotlin.math.abs(a.length - b.length) > 1) return false
        var i = 0
        var j = 0
        var slack = 1
        while (i < a.length && j < b.length) {
            if (a[i] == b[j]) { i++; j++; continue }
            if (slack == 0) return false
            slack = 0
            when {
                a.length > b.length -> i++
                a.length < b.length -> j++
                else -> { i++; j++ }
            }
        }
        return slack - (a.length - i) - (b.length - j) >= 0
    }

    fun stripWake(text: String): String {
        val normalised = normalise(text)
        val first = normalised.substringBefore(' ')
        if (!isWake(first)) return text.trim()
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
