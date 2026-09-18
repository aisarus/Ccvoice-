package com.voiceshell

/**
 * Знаки ударения на пути к синтезу.
 *
 * Демон присылает реплику дважды: `text` — чистый, для экрана, и `spoken` —
 * со знаком `+` перед ударной гласной. RHVoice понимает такой знак сам;
 * системный движок прочитал бы его вслух как «плюс», поэтому для него знаки
 * снимаются. Комбинирующее ударение — запасной вариант на случай, если
 * конкретная сборка RHVoice `+` не понимает: это проверяется на слух за
 * десять секунд, поэтому выбор оставлен человеку.
 */
object Stress {

    const val AUTO = "auto"
    const val PLUS = "plus"
    const val ACUTE_STYLE = "acute"
    const val OFF = "off"
    val STYLES = listOf(AUTO, PLUS, ACUTE_STYLE, OFF)

    private const val MARK = '+'
    private const val ACUTE = '́'
    private const val VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"

    /** Ключ подписи кнопки: слова выбирает система по локали. */
    fun label(style: String): Int = when (style) {
        PLUS -> R.string.stress_plus
        ACUTE_STYLE -> R.string.stress_acute
        OFF -> R.string.stress_off
        else -> R.string.stress_auto
    }

    fun next(style: String): String = STYLES[(STYLES.indexOf(style).coerceAtLeast(0) + 1) % STYLES.size]

    /** Что именно отправить в синтез. */
    fun render(text: String, style: String, engine: String): String = when (style) {
        PLUS -> text
        ACUTE_STYLE -> acute(text)
        OFF -> strip(text)
        // Авто: RHVoice знает «+» сам, всем остальным знаки только мешают.
        else -> if (engine.contains("rhvoice", ignoreCase = true)) text else strip(text)
    }

    fun strip(text: String): String = text.filter { it != MARK && it != ACUTE }

    /** «комм+ит» → «коммит» с комбинирующим ударением после гласной. */
    fun acute(text: String): String {
        val out = StringBuilder(text.length)
        var i = 0
        while (i < text.length) {
            val char = text[i]
            if (char == MARK && i + 1 < text.length && VOWELS.contains(text[i + 1])) {
                out.append(text[i + 1]).append(ACUTE)
                i += 2
                continue
            }
            out.append(char)
            i++
        }
        return out.toString()
    }
}
