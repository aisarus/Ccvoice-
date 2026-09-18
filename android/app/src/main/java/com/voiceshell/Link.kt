package com.voiceshell

import android.content.Context

/**
 * Что со связью прямо сейчас.
 *
 * Отдельным состоянием, а не строкой в журнале: одну и ту же строку «нет связи»
 * человек видел и когда телефон выпал из сети, и когда сервер отказал по
 * токену, и чинил не то. Строка идёт и на экран, и в уведомление, поэтому у
 * состояния лежит не текст, а ключ ресурса: слова выбирает система по локали.
 */
enum class LinkState(val label: Int) {
    UNKNOWN(R.string.link_unknown),
    OFF(R.string.link_off),
    CONNECTING(R.string.link_connecting),
    ONLINE(R.string.link_online),
    NO_NETWORK(R.string.link_no_network),
    NO_SERVER(R.string.link_no_server),
    REFUSED(R.string.link_refused),
    CLEARTEXT(R.string.link_cleartext),
}

/**
 * Разбор обрыва и пауза до следующей попытки.
 *
 * Пауза была постоянной: телефон в метро долбился в мёртвый адрес раз в четыре
 * секунды и съедал батарею, а вернувшись домой — те же четыре секунды ждал
 * впустую. Поэтому пауза нарастает, у каждой причины своя, и у всех есть
 * потолок, чтобы приложение не «уснуло» навсегда.
 */
object Link {

    /**
     * Почему оборвалось.
     *
     * Ответ сервера (любой HTTP-код) — это уже разговор: адрес верный, отказ
     * осмысленный, и чинить надо токен или путь. Молчание при живой сети —
     * сервер не поднят или закрыт фаерволом. Молчание без сети — чинить нечего,
     * надо ждать сеть.
     */
    fun classify(message: String?, httpCode: Int, online: Boolean): LinkState {
        val text = message.orEmpty()
        if (text.contains("CLEARTEXT", ignoreCase = true)) return LinkState.CLEARTEXT
        if (httpCode > 0) return LinkState.REFUSED
        if (!online) return LinkState.NO_NETWORK
        return LinkState.NO_SERVER
    }

    /** Ключ подсказки: у каждой причины своя, общей «ошибки связи» здесь нет. */
    fun hintText(state: LinkState): Int = when (state) {
        LinkState.OFF -> R.string.link_hint_off
        LinkState.UNKNOWN -> R.string.link_hint_unknown
        LinkState.CONNECTING -> R.string.link_hint_connecting
        LinkState.ONLINE -> R.string.link_hint_online
        LinkState.NO_NETWORK -> R.string.link_hint_no_network
        LinkState.NO_SERVER -> R.string.link_hint_no_server
        LinkState.REFUSED -> R.string.link_hint_refused
        LinkState.CLEARTEXT -> R.string.link_hint_cleartext
    }

    /** Строка человеку: что именно случилось и что с этим делать. */
    fun hint(context: Context, state: LinkState, message: String?, httpCode: Int): String =
        when (state) {
            // Текст ошибки от okhttp — единственное, что отличает один мёртвый
            // адрес от другого, поэтому он идёт в скобках как есть.
            LinkState.NO_SERVER -> {
                val base = context.getString(hintText(state))
                message?.takeIf { it.isNotBlank() }
                    ?.let { context.getString(R.string.link_hint_detail, base, it) }
                    ?: base
            }
            LinkState.REFUSED -> context.getString(hintText(state), httpCode)
            else -> context.getString(hintText(state))
        }

    /**
     * Через сколько пробовать снова.
     *
     * Отказ по токену сам не пройдёт — туда ходим редко. Нет сети — ждём долго,
     * потому что возвращение сети приложение и так увидит сразу. Сервер не
     * отвечает — это чаще всего перезапуск демона, и туда стоит вернуться
     * быстро.
     */
    fun delayMs(state: LinkState, attempt: Int): Long {
        val first: Long
        val cap: Long
        when (state) {
            LinkState.REFUSED, LinkState.CLEARTEXT -> { first = 30_000L; cap = 300_000L }
            LinkState.NO_NETWORK -> { first = 15_000L; cap = 120_000L }
            else -> { first = 2_000L; cap = 60_000L }
        }
        var delay = first
        // Умножением, а не степенью: так не переполнится даже на сотой попытке.
        repeat(attempt.coerceIn(0, 32)) { if (delay < cap) delay *= 2 }
        return delay.coerceAtMost(cap)
    }
}
