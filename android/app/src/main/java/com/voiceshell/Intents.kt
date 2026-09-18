package com.voiceshell

/**
 * Разбор реплики на телефоне: обращение и «стоп».
 *
 * «Стоп» распознаётся всегда — до обращения, во время ответа и во время работы,
 * потому что остановка не должна зависеть от круга по сети.
 */
object Intents {
    val WAKE = listOf("клод", "клода", "клоуд", "клауд", "claude", "клот", "клоуде",
                      "claudio", "clod")

    /** Обращение по-китайски: там нет пробелов, слово ищется в начале строки. */
    private val WAKE_CJK = listOf("克劳德", "克劳德你好")

    /**
     * От этих слов отсчитывается допуск в одну букву.
     *
     * Только от них: остальное в списке — уже расслышанное с ошибкой, и допуск
     * поверх ошибки давал бы две буквы разницы. Тогда «крот» (одна буква от
     * «клот») будил бы приложение, и оно отзывалось бы на чужой разговор.
     */
    private val WAKE_ROOTS = listOf("клод", "клоуд", "клауд", "claude")
    /** Слова, которыми начинают команду: их нельзя принимать за обращение. */
    private val NOT_WAKE = setOf("код", "чат", "кот", "что", "как", "код?")
    private val STOP_WORK = listOf(
        "стоп работу", "стоп работа", "останови работу", "останови", "прекрати",
        // Про работу сказано прямо — это остановка, а не откат.
        "отмени работу", "отмена работы",
        "stop working", "stop the work", "abort", "cancel",
        "detén el trabajo", "deten el trabajo", "para el trabajo", "cancela"
    )

    /**
     * «Стоп» по-китайски: без пробелов, поэтому сравнение по началу строки.
     *
     * Отдельно от остальных ровно поэтому: `startsWith("$it ")` там не
     * сработало бы никогда, а `bare == it` требовал бы идеально чистой реплики.
     */
    private val STOP_WORK_CJK = listOf("停止工作", "别做了", "取消任务")
    private val STOP_VOICE_CJK = listOf("停下", "安静", "够了", "别说了", "停")

    /**
     * Остановка только целой репликой, без продолжения.
     *
     * «Отмени» — это «прекрати работать», а «отмени последнее» и «отмена
     * последнего» — это откат, который умеет делать демон. Пока эти слова
     * ловились вместе с продолжением, откат не доходил до демона никогда:
     * телефон превращал его в прерывание и сам же о нём забывал.
     */
    private val STOP_WORK_ALONE = listOf("отмени", "отмена")
    private val STOP_VOICE = listOf(
        "стоп", "тихо", "хватит", "замолчи",
        "stop", "quiet", "enough", "shut up",
        "silencio", "basta", "cállate", "callate", "para"
    )

    private fun isCjk(text: String): Boolean = text.any { it in '\u4e00'..'\u9fff' }

    fun normalise(text: String): String =
        text.lowercase().replace(Regex("[^\\p{L}\\p{N}\\s]"), " ").replace(Regex("\\s+"), " ").trim()

    fun hasWake(text: String): Boolean {
        val bare = normalise(text)
        if (WAKE_CJK.any { bare.startsWith(it) }) return true
        return isWake(bare.substringBefore(' '))
    }

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
        WAKE_CJK.firstOrNull { normalised.startsWith(it) }?.let { wake ->
            return normalised.removePrefix(wake).trim().ifBlank { text.trim() }
        }
        val first = normalised.substringBefore(' ')
        if (!isWake(first)) return text.trim()
        return normalised.substringAfter(' ', "").trim().ifBlank { text.trim() }
    }

    /** Смена языка голосом: «Клод, английский». Возвращает код языка или null. */
    private val LANGUAGES = mapOf(
        "ru-RU" to listOf("русский", "по русски", "russian", "рашн", "ruso", "俄语"),
        "en-US" to listOf("английский", "по английски", "english", "инглиш",
                          "inglés", "ingles", "英语"),
        "es-ES" to listOf("испанский", "по испански", "spanish", "español", "espanol",
                          "西班牙语"),
        "zh-CN" to listOf("китайский", "по китайски", "chinese", "chino",
                          "中文", "汉语", "普通话"),
        "he-IL" to listOf("иврит", "на иврите", "hebrew", "hebreo", "עברית", "希伯来语")
    )

    /** «Клод, как спросил» — снять закрепление и отвечать на языке вопроса. */
    const val ANY_LANGUAGE = "auto"
    private val ANY_WORDS = listOf(
        "как спросил", "как спрошу", "любой язык", "как я сказал", "автоматически",
        "auto", "automatic", "same language", "as i asked",
        "automático", "automatico", "el mismo idioma",
        "自动", "跟我一样"
    )

    fun languageSwitch(text: String): String? {
        val bare = stripWake(normalise(text))
            .removePrefix("переключись на ").removePrefix("переключись ")
            .removePrefix("говори ").removePrefix("switch to ").removePrefix("speak ")
            .removePrefix("cambia a ").removePrefix("habla ")
            .removePrefix("说").removePrefix("切换到").trim()
        if (ANY_WORDS.any {
                bare == it || bare.startsWith("$it ") || (isCjk(it) && bare.startsWith(it))
            }) return ANY_LANGUAGE
        for ((code, words) in LANGUAGES) {
            if (words.any { bare == it || bare.startsWith("$it ") }) return code
            if (words.any { isCjk(it) && bare.startsWith(it) }) return code
        }
        return null
    }

    /** Что сказать вслух о новом языке ответа — на нём же. */
    fun switchNotice(replyLanguage: String, recognitionLanguage: String): String {
        val spoken = replyLanguage.ifBlank { recognitionLanguage }
        val pinned = mapOf(
            "ru-RU" to "Отвечаю по-русски.",
            "en-US" to "Answering in English.",
            "es-ES" to "Respondo en español.",
            "zh-CN" to "我用中文回答。",
            "he-IL" to "עונה בעברית."
        )
        val auto = mapOf(
            "ru-RU" to "Отвечаю на языке вопроса.",
            "en-US" to "Answering in whatever language you use.",
            "es-ES" to "Respondo en el idioma en que preguntes.",
            "zh-CN" to "你用什么语言问，我就用什么语言回答。",
            "he-IL" to "עונה בשפה שבה שאלת."
        )
        val table = if (replyLanguage.isBlank()) auto else pinned
        return table[spoken] ?: table["en-US"].orEmpty()
    }

    /** Язык ответа определяется по письменности, а не по настройке. */
    fun scriptLanguage(text: String, fallback: String): String = when {
        text.any { it in '\u0590'..'\u05FF' } -> "he-IL"
        isCjk(text) -> "zh-CN"
        text.any { it in '\u0400'..'\u04FF' } -> "ru-RU"
        // Испанский от английского письменностью не отличить, и гадать по
        // словам здесь незачем: выбранный язык реплик уже говорит, какой из
        // двух имеется в виду.
        text.any { it in 'a'..'z' || it in 'A'..'Z' } ->
            if (fallback.startsWith("es")) "es-ES" else "en-US"
        else -> fallback
    }

    /** "work", "voice" или null. */
    fun stopIntent(text: String): String? {
        val bare = stripWake(normalise(text))
        if (STOP_WORK_ALONE.any { bare == it }) return "work"
        STOP_WORK.firstOrNull { bare == it || bare.startsWith("$it ") }?.let { return "work" }
        STOP_WORK_CJK.firstOrNull { bare.startsWith(it) }?.let { return "work" }
        STOP_VOICE.firstOrNull { bare == it || bare.startsWith("$it ") }?.let { return "voice" }
        STOP_VOICE_CJK.firstOrNull { bare.startsWith(it) }?.let { return "voice" }
        return null
    }
}
