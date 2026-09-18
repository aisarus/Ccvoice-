package com.voiceshell

import android.content.Context

/**
 * Насколько пункт плох.
 *
 * `BAD` — без этого не заработает вовсе. `WARN` — заработает, но подведёт:
 * именно в этот разряд попадает всё, из-за чего приложение «работает через
 * раз», а человек не понимает почему.
 */
enum class Ready { OK, WARN, BAD }

/**
 * Строка проверки: что не так, насколько плохо и чем чинится.
 *
 * Подпись, пояснение и кнопка лежат ключами ресурсов, а не словами: сам разбор
 * готовности к языку интерфейса отношения не имеет и проверяется на JVM.
 */
data class Check(
    val id: String,
    val title: Int,
    val state: Ready,
    val detail: Int,
    /** Подпись кнопки починки, или null — если чинить здесь нечем. */
    val fix: Int?,
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
            MIC, R.string.readiness_mic,
            if (mic) Ready.OK else Ready.BAD,
            if (mic) R.string.readiness_mic_ok else R.string.readiness_mic_bad,
            if (mic) null else R.string.readiness_fix_grant,
        ),
        Check(
            NOTIFY, R.string.readiness_notifications,
            if (notifications) Ready.OK else Ready.BAD,
            if (notifications) R.string.readiness_notifications_ok
            else R.string.readiness_notifications_bad,
            if (notifications) null else R.string.readiness_fix_enable,
        ),
        Check(
            BACKGROUND, R.string.readiness_background,
            if (background) Ready.OK else Ready.BAD,
            if (background) R.string.readiness_background_ok
            else R.string.readiness_background_bad,
            if (background) null else R.string.readiness_fix_allow,
        ),
        voiceCheck(engine, engines),
        linkCheck(link),
    )

    private fun voiceCheck(engine: String, engines: List<String>): Check = when {
        engines.isEmpty() -> Check(
            VOICE, R.string.readiness_tts, Ready.BAD,
            R.string.readiness_tts_none, R.string.readiness_fix_install,
        )
        engine.isBlank() -> Check(
            VOICE, R.string.readiness_tts, Ready.WARN,
            R.string.readiness_tts_default, R.string.readiness_fix_pick,
        )
        engine !in engines -> Check(
            VOICE, R.string.readiness_tts, Ready.BAD,
            R.string.readiness_tts_gone, R.string.readiness_fix_pick,
        )
        else -> Check(VOICE, R.string.readiness_tts, Ready.OK, R.string.readiness_tts_ok, null)
    }

    private fun linkCheck(link: LinkState): Check = when (link) {
        LinkState.ONLINE -> Check(
            LINK, R.string.readiness_link, Ready.OK, R.string.readiness_link_online, null,
        )
        LinkState.CONNECTING -> Check(
            LINK, R.string.readiness_link, Ready.WARN, R.string.readiness_link_connecting, null,
        )
        LinkState.UNKNOWN -> Check(
            LINK, R.string.readiness_link, Ready.WARN,
            R.string.readiness_link_unknown, R.string.readiness_fix_start,
        )
        LinkState.OFF -> Check(
            LINK, R.string.readiness_link, Ready.BAD, R.string.readiness_link_off, null,
        )
        LinkState.NO_NETWORK -> Check(
            LINK, R.string.readiness_link, Ready.BAD,
            R.string.readiness_link_no_network, R.string.readiness_fix_network,
        )
        LinkState.NO_SERVER -> Check(
            LINK, R.string.readiness_link, Ready.BAD,
            R.string.readiness_link_no_server, R.string.readiness_fix_retry,
        )
        LinkState.REFUSED -> Check(
            LINK, R.string.readiness_link, Ready.BAD,
            R.string.readiness_link_refused, R.string.readiness_fix_retry,
        )
        LinkState.CLEARTEXT -> Check(
            LINK, R.string.readiness_link, Ready.BAD,
            R.string.readiness_link_cleartext, R.string.readiness_fix_retry,
        )
    }

    fun mark(state: Ready): String = when (state) {
        Ready.OK -> "✓"
        Ready.WARN -> "!"
        Ready.BAD -> "✗"
    }

    /** Стоит ли вообще говорить в телефон: худшее из состояний. */
    fun verdict(checks: List<Check>): Ready = when {
        checks.any { it.state == Ready.BAD } -> Ready.BAD
        checks.any { it.state == Ready.WARN } -> Ready.WARN
        else -> Ready.OK
    }

    fun line(context: Context, check: Check): String = context.getString(
        R.string.readiness_line,
        mark(check.state), context.getString(check.title), context.getString(check.detail),
    )

    /** Одна строка сверху: стоит ли вообще говорить в телефон. */
    fun summary(context: Context, checks: List<Check>): String = when (verdict(checks)) {
        Ready.BAD -> context.getString(
            R.string.readiness_not_ready,
            checks.filter { it.state == Ready.BAD }
                .joinToString(", ") { context.getString(it.title) },
        )
        Ready.WARN -> context.getString(R.string.readiness_with_warnings)
        Ready.OK -> context.getString(R.string.readiness_all_ready)
    }
}
