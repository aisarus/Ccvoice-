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
        assertTrue(Link.hint(state, null, 0).contains("https"))
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
    fun everyHintNamesWhatToDoNext() {
        assertTrue(Link.hint(LinkState.NO_SERVER, null, 0).contains("адрес"))
        assertTrue(Link.hint(LinkState.REFUSED, null, 401).contains("токен"))
        assertTrue(Link.hint(LinkState.NO_NETWORK, null, 0).contains("сеть"))
        assertTrue(Link.hint(LinkState.OFF, null, 0).contains("токен"))
    }

    @Test
    fun theLabelAlwaysStartsWithTheWordLink() {
        // Строка уходит в подзаголовок уведомления рядом с фазой, и в одиночку
        // она должна читаться однозначно.
        for (state in LinkState.values()) assertTrue(state.label.startsWith("связь: "))
    }
}
