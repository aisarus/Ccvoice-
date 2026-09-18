package com.voiceshell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * «Нет связи» — строка, за которой пряталось три разных несчастья: телефон вне
 * сети, демон не запущен и неверный токен. Чинится каждое по-своему, поэтому
 * они и разведены.
 */
class LinkTest {

    @Test
    fun aServerThatAnsweredWithRefusalIsNotTheSameAsNoNetwork() {
        assertEquals(
            LinkState.REFUSED,
            Link.classify("Expected HTTP 101 response", httpCode = 401, online = true)
        )
        assertEquals(
            LinkState.NO_NETWORK,
            Link.classify("Failed to connect to /10.0.0.2:8787", httpCode = 0, online = false)
        )
        assertEquals(
            LinkState.NO_SERVER,
            Link.classify("Failed to connect to /10.0.0.2:8787", httpCode = 0, online = true)
        )
    }

    @Test
    fun theSystemsCleartextRefusalIsRecognisedBeforeAnythingElse() {
        // Эта ошибка приходит без ответа сервера и при живой сети — по общим
        // правилам выглядела бы как «демон не запущен», и человек чинил бы не то.
        val state = Link.classify(
            "CLEARTEXT communication to 10.0.0.2 not permitted", httpCode = 0, online = true
        )
        assertEquals(LinkState.CLEARTEXT, state)
        assertEquals(R.string.link_hint_cleartext, Link.hintText(state))
    }

    @Test
    fun thePauseGrowsWithEachFailedAttempt() {
        val first = Link.delayMs(LinkState.NO_SERVER, 0)
        val third = Link.delayMs(LinkState.NO_SERVER, 2)
        assertTrue("$first -> $third", third > first)
    }

    @Test
    fun thePauseNeverGrowsPastTheCeiling() {
        // Иначе телефон, проведший ночь без сети, утром ждал бы часами.
        for (state in LinkState.values()) {
            assertTrue(state.name, Link.delayMs(state, 100) <= 300_000L)
            assertEquals(
                "потолок должен быть достигнут: ${state.name}",
                Link.delayMs(state, 100), Link.delayMs(state, 1000)
            )
        }
    }

    @Test
    fun aRejectedTokenIsRetriedFarMoreRarelyThanADroppedLink() {
        // Токен сам не исправится: долбиться в него раз в две секунды — это
        // только севшая батарея.
        assertTrue(Link.delayMs(LinkState.REFUSED, 0) > Link.delayMs(LinkState.NO_SERVER, 0) * 5)
    }

    @Test
    fun everyBreakageGetsItsOwnHint() {
        // Слова подсказок живут в ресурсах и зависят от языка телефона, а вот
        // то, что у каждой беды подсказка своя, от языка не зависит: общая
        // «ошибка связи» на все случаи — ровно то, из-за чего чинили не то.
        assertEquals(R.string.link_hint_no_server, Link.hintText(LinkState.NO_SERVER))
        assertEquals(R.string.link_hint_refused, Link.hintText(LinkState.REFUSED))
        assertEquals(R.string.link_hint_no_network, Link.hintText(LinkState.NO_NETWORK))
        assertEquals(R.string.link_hint_off, Link.hintText(LinkState.OFF))
        val hints = LinkState.values().map { Link.hintText(it) }
        assertEquals(LinkState.values().size, hints.toSet().size)
    }

    @Test
    fun everyStateHasItsOwnLabel() {
        // Строка уходит в подзаголовок уведомления рядом с фазой, и в одиночку
        // она должна читаться однозначно. Что она про связь и что состояния не
        // делят одну надпись на двоих, проверяется здесь; сами слова — в
        // StringsTest, по всем четырём языкам сразу.
        val labels = LinkState.values().map { it.label }
        assertEquals(LinkState.values().size, labels.toSet().size)
    }
}
