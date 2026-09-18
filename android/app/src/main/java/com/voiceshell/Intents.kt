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

    /**
     * Что делать с тем, как оболочка слушает.
     *
     * `language` — новый язык распознавания, null — оставить как есть.
     * `multilingual` — слушать ли несколько языков сразу, null — не трогать.
     */
    data class Listen(val language: String?, val multilingual: Boolean?)

    private val LISTEN_ALL = listOf(
        "слушай все языки", "слушай любой язык", "понимай все языки",
        "listen to all languages", "listen in any language", "understand every language",
        "escucha todos los idiomas", "听所有语言", "什么语言都听"
    )
    private val LISTEN_ONE = listOf(
        "слушай только", "слушай один язык", "только один язык",
        "listen only", "listen to one language", "one language only",
        "escucha solo", "只听一种语言", "只听"
    )
    /**
     * Начала фраз «слушай …»: после них ожидается название языка.
     *
     * Предлог в список не входит нарочно. Пока здесь стояло «слушай на»,
     * оно срабатывало раньше голого «слушай» и съедало «на», а в списке
     * языков лежит именно «на иврите» — и «слушай на иврите» не понималось
     * вовсе. Предлог снимает `languageIn`, и снимает после того, как
     * попробует название целиком.
     */
    private val LISTEN_OPENERS = listOf(
        "слушай", "распознавай",
        "listen to", "listen", "recognise", "recognize",
        "escucha", "听", "识别"
    )

    /**
     * «Слушай все языки», «слушай только русский», «слушай иврит».
     *
     * Отдельно от `languageSwitch`: «Клод, английский» меняет язык ответа, а
     * «Клод, слушай английский» — язык микрофона. Разбирается одной функцией
     * нарочно: «слушай только английский» — это обе перемены сразу, и при
     * двух независимых проверках одна из них всегда съедала бы вторую.
     */
    fun listenSwitch(text: String): Listen? {
        val bare = stripWake(normalise(text))
        fun hit(phrases: List<String>) = phrases.firstOrNull {
            bare == it || bare.startsWith("$it ") || (isCjk(it) && bare.startsWith(it))
        }
        if (hit(LISTEN_ALL) != null) return Listen(null, true)
        hit(LISTEN_ONE)?.let { phrase ->
            // «Слушай только английский» — это и один язык, и какой именно.
            return Listen(languageIn(bare.removePrefix(phrase)), false)
        }
        hit(LISTEN_OPENERS)?.let { phrase ->
            val named = languageIn(bare.removePrefix(phrase)) ?: return null
            return Listen(named, null)
        }
        return null
    }

    /**
     * Код языка, названный в этом куске речи, или null.
     *
     * Сначала пробуем то, что сказано, целиком: «на иврите» и «по русски»
     * лежат в списке языков именно так. Только если целиком не узналось,
     * снимаем предлог — иначе «на иврите» превращалось в «иврите», которого
     * в списке нет, и название языка терялось на ровном месте.
     */
    private fun languageIn(rest: String): String? {
        val bare = rest.trim()
        return named(bare) ?: named(
            bare.removePrefix("по ").removePrefix("на ")
                .removePrefix("in ").removePrefix("en ").trim()
        )
    }

    private fun named(bare: String): String? {
        for ((code, words) in LANGUAGES) {
            if (words.any { bare == it || bare.startsWith("$it ") }) return code
            if (words.any { isCjk(it) && bare.startsWith(it) }) return code
        }
        return null
    }

    /**
     * Что делать со вторым ухом.
     *
     * `open` — открыть или закрыть, `language` — на каком языке распознавать
     * комнату (null — оставить как есть). Названный язык не открывает ухо
     * отдельной командой: «второе ухо на иврите» — это и «слушай вокруг», и
     * «вокруг говорят на иврите», и разделять их значило бы требовать две
     * фразы там, где человек говорит одну.
     */
    data class SecondEar(val open: Boolean, val language: String?)

    /**
     * Фразы второго уха — те же, что в `lexicon.py` демона.
     *
     * Список продублирован нарочно: телефон обязан узнать команду сам, иначе
     * он не перестанет слушать комнату (или не начнёт) до ответа по сети. Но
     * узнать он должен ровно то же, что и демон, — иначе один из двоих будет
     * считать ухо открытым, а второй закрытым.
     */
    private val EAR_ON = listOf(
        "второе ухо", "включи второе ухо", "открой второе ухо",
        "слушай вокруг", "слушай комнату", "слушай что вокруг",
        "second ear", "turn on the second ear", "open the second ear",
        "listen around", "listen to the room",
        "segundo oído", "segundo oido", "escucha alrededor",
        "第二只耳朵", "听周围"
    )
    private val EAR_OFF = listOf(
        "выключи второе ухо", "убери второе ухо", "закрой второе ухо",
        "хватит слушать вокруг", "перестань слушать вокруг", "без второго уха",
        "turn off the second ear", "close the second ear", "stop the second ear",
        "stop listening around", "no second ear",
        "apaga el segundo oído", "apaga el segundo oido", "deja de escuchar alrededor",
        "关掉第二只耳朵", "别听周围了"
    )

    /**
     * «Клод, второе ухо» · «выключи второе ухо» · «второе ухо на иврите».
     *
     * Проверяется раньше «стопа» — и это не вкусовщина: «хватит слушать
     * вокруг» начинается со слова «хватит», которым гасят голос. При обратном
     * порядке ухо не закрылось бы никогда, а человек, попросивший перестать
     * слушать чужих, получил бы тишину вместо закрытого уха.
     */
    fun secondEar(text: String): SecondEar? {
        val bare = stripWake(normalise(text))
        fun hit(phrases: List<String>) = phrases.firstOrNull {
            bare == it || bare.startsWith("$it ") || (isCjk(it) && bare.startsWith(it))
        }
        if (hit(EAR_OFF) != null) return SecondEar(false, null)
        hit(EAR_ON)?.let { phrase ->
            return SecondEar(true, languageIn(bare.removePrefix(phrase)))
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
