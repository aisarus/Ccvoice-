package com.voiceshell

/**
 * Что со связью прямо сейчас.
 *
 * Отдельным состоянием, а не строкой в журнале: одну и ту же строку «нет связи»
 * человек видел и когда телефон выпал из сети, и когда сервер отказал по
 * токену, и чинил не то. Строка идёт и на экран, и в уведомление.
 */
enum class LinkState(val label: String) {
    UNKNOWN("связь: служба не запущена"),
    OFF("связь: не настроена"),
    CONNECTING("связь: подключаюсь"),
    ONLINE("связь: есть"),
    NO_NETWORK("связь: нет сети"),
    NO_SERVER("связь: сервер не отвечает"),
    REFUSED("связь: сервер отказал"),
    CLEARTEXT("связь: http запрещён системой"),
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

    /** Строка человеку: что именно случилось и что с этим делать. */
    fun hint(state: LinkState, message: String?, httpCode: Int): String = when (state) {
        LinkState.OFF -> "не настроено: укажи адрес сервиса и токен"
        LinkState.UNKNOWN -> "служба не запущена"
        LinkState.CONNECTING -> "подключаюсь к серверу…"
        LinkState.ONLINE -> "подключено"
        LinkState.NO_NETWORK -> "нет сети: телефон не в интернете — жду сеть"
        LinkState.NO_SERVER ->
            "сервер не отвечает: проверь адрес, порт и что демон запущен" +
                message.orEmpty().takeIf { it.isNotBlank() }?.let { " ($it)" }.orEmpty()
        LinkState.REFUSED -> "сервер отказал (HTTP $httpCode): проверь токен и адрес"
        LinkState.CLEARTEXT -> "http запрещён системой: нужен https или ws на доверенном адресе"
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
