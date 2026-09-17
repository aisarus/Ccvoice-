package com.voiceshell

/**
 * Насколько пункт плох.
 *
 * `BAD` — без этого не заработает вовсе. `WARN` — заработает, но подведёт:
 * именно в этот разряд попадает всё, из-за чего приложение «работает через
 * раз», а человек не понимает почему.
 */
enum class Ready { OK, WARN, BAD }

/** Строка проверки: что не так, насколько плохо и чем чинится. */
data class Check(
    val id: String,
    val title: String,
    val state: Ready,
    val detail: String,
    /** Подпись кнопки починки, или null — если чинить здесь нечем. */
    val fix: String?,
)

/**
 * Проверка готовности на главном экране.
 *
 * Разрешение на микрофон выдано, но служба спит; уведомления выключены, и
 * Android гасит службу; движок синтеза не выбран, и вместо голоса тишина —
 * снаружи всё это выглядит одинаково: «не работает». Поэтому каждый пункт
 * назван словами и у каждого есть кнопка.
 */
object Readiness {

    const val MIC = "mic"
    const val NOTIFY = "notify"
    const val BACKGROUND = "background"
    const val VOICE = "voice"
    const val LINK = "link"

    fun checks(
        mic: Boolean,
        notifications: Boolean,
        background: Boolean,
        engine: String,
        engines: List<String>,
        link: LinkState,
    ): List<Check> = listOf(
        Check(
            MIC, "микрофон",
            if (mic) Ready.OK else Ready.BAD,
            if (mic) "разрешён" else "без него служба не имеет права запуститься",
            if (mic) null else "выдать",
        ),
        Check(
            NOTIFY, "уведомления",
            if (notifications) Ready.OK else Ready.BAD,
            if (notifications) "включены"
            else "без них Android не даёт держать фоновую службу",
            if (notifications) null else "включить",
        ),
        Check(
            BACKGROUND, "работа в фоне",
            if (background) Ready.OK else Ready.BAD,
            if (background) "разрешена"
            else "иначе система усыпит службу через несколько минут после экрана",
            if (background) null else "разрешить",
        ),
        voiceCheck(engine, engines),
        linkCheck(link),
    )

    private fun voiceCheck(engine: String, engines: List<String>): Check = when {
        engines.isEmpty() -> Check(
            VOICE, "синтез речи", Ready.BAD,
            "движков в системе нет — ответ нечем произнести", "установить",
        )
        engine.isBlank() -> Check(
            VOICE, "синтез речи", Ready.WARN,
            "движок не выбран — говорит системный робот", "выбрать",
        )
        engine !in engines -> Check(
            VOICE, "синтез речи", Ready.BAD,
            "выбранный движок пропал из системы", "выбрать",
        )
        else -> Check(VOICE, "синтез речи", Ready.OK, "выбран", null)
    }

    private fun linkCheck(link: LinkState): Check = when (link) {
        LinkState.ONLINE -> Check(LINK, "связь с сервером", Ready.OK, "демон отвечает", null)
        LinkState.CONNECTING ->
            Check(LINK, "связь с сервером", Ready.WARN, "подключаюсь…", null)
        LinkState.UNKNOWN ->
            Check(LINK, "связь с сервером", Ready.WARN, "служба не запущена", "запустить")
        LinkState.OFF ->
            Check(LINK, "связь с сервером", Ready.BAD, "не заданы адрес или токен", null)
        LinkState.NO_NETWORK ->
            Check(LINK, "связь с сервером", Ready.BAD, "телефон не в сети", "сеть")
        LinkState.NO_SERVER -> Check(
            LINK, "связь с сервером", Ready.BAD,
            "сервер не отвечает — проверь адрес и что демон запущен", "повторить",
        )
        LinkState.REFUSED -> Check(
            LINK, "связь с сервером", Ready.BAD,
            "сервер отказал — скорее всего не тот токен", "повторить",
        )
        LinkState.CLEARTEXT -> Check(
            LINK, "связь с сервером", Ready.BAD,
            "адрес по http запрещён системой — нужен https", "повторить",
        )
    }

    fun mark(state: Ready): String = when (state) {
        Ready.OK -> "✓"
        Ready.WARN -> "!"
        Ready.BAD -> "✗"
    }

    fun line(check: Check): String = "${mark(check.state)} ${check.title} — ${check.detail}"

    /** Одна строка сверху: стоит ли вообще говорить в телефон. */
    fun summary(checks: List<Check>): String {
        val broken = checks.filter { it.state == Ready.BAD }
        val shaky = checks.count { it.state == Ready.WARN }
        return when {
            broken.isNotEmpty() -> "не готово: " + broken.joinToString(", ") { it.title }
            shaky > 0 -> "готово, но есть замечания"
            else -> "всё готово"
        }
    }
}
