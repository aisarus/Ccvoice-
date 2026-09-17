package com.voiceshell

import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * «Отзывается через раз» — это чаще всего не глухота, а одна перепутанная
 * буква: маленькая модель слышит «клот» и «плод» вместо «Клод».
 */
class IntentsTest {

    @Test
    fun theWakeWordSurvivesOneWrongLetter() {
        for (heard in listOf("клод", "клот", "клад", "плод", "клоуд", "клауд", "claude")) {
            assertTrue(heard, Intents.hasWake("$heard покажи логи"))
        }
    }

    @Test
    fun theStartOfACommandIsNotTheWakeWord() {
        // «Код …» и «чат …» — это префиксы цели, их нельзя съедать.
        for (heard in listOf("код", "чат", "кот", "что", "как")) {
            assertFalse(heard, Intents.hasWake("$heard покажи логи"))
        }
    }

    @Test
    fun twoWrongLettersAreTooMany() {
        // «крот» — одна буква от «клот», а «клот» само уже расслышано с
        // ошибкой. Если отсчитывать допуск и от таких вариантов, приложение
        // начнёт просыпаться посреди чужого разговора.
        assertFalse(Intents.hasWake("крот покажи логи"))
        assertFalse(Intents.hasWake("привет покажи логи"))
        assertFalse(Intents.hasWake("стоп покажи логи"))
    }

    @Test
    fun theWakeWordIsCutOffTheCommand() {
        assertEquals("покажи логи", Intents.stripWake("Клот, покажи логи"))
        assertEquals("покажи логи", Intents.stripWake("покажи логи"))
    }

    @Test
    fun stopIsHeardWithOrWithoutTheWakeWord() {
        assertEquals("voice", Intents.stopIntent("стоп"))
        assertEquals("work", Intents.stopIntent("клод останови работу"))
    }

    @Test
    fun stoppingDoesNotSwallowTheRollback() {
        // Все семь фраз отката из checkpoints.py демона. Телефон ловил «отмени»
        // и «отмена» вместе с продолжением, превращал «отмени последнее» в
        // прерывание работы — и откат не случался никогда.
        val undo = listOf(
            "откати последнее", "откати", "отмени последнее", "отмени изменения",
            "верни как было", "верни обратно", "отмена последнего",
        )
        for (phrase in undo) {
            assertNull(phrase, Intents.stopIntent(phrase))
            assertNull("с обращением: $phrase", Intents.stopIntent("клод $phrase"))
        }
    }

    @Test
    fun stoppingStillStopsWhenNothingFollowsTheWord() {
        assertEquals("work", Intents.stopIntent("отмени"))
        assertEquals("work", Intents.stopIntent("отмена"))
        assertEquals("work", Intents.stopIntent("останови работу"))
        assertEquals("work", Intents.stopIntent("прекрати"))
        assertEquals("voice", Intents.stopIntent("стоп"))
        // Про работу сказано прямо — откатывать нечего, надо останавливать.
        assertEquals("work", Intents.stopIntent("отмени работу"))
        assertEquals("work", Intents.stopIntent("отмена работы"))
    }

    @Test
    fun theAnswerIsSpokenInTheLanguageItCameIn() {
        // Ответ на иврите, прочитанный русским голосом, звучит как треск:
        // язык синтеза выбирается по письменности самого ответа.
        assertEquals("he-IL", Intents.scriptLanguage("הבדיקות עוברות", "ru-RU"))
        assertEquals("ru-RU", Intents.scriptLanguage("Тесты проходят", "en-US"))
        assertEquals("en-US", Intents.scriptLanguage("All tests pass", "ru-RU"))
        // Цифры письменности не имеют — остаётся настройка.
        assertEquals("ru-RU", Intents.scriptLanguage("42", "ru-RU"))
    }

    @Test
    fun theLanguageSwitchIsNotTriggeredByAnOrdinaryCommand() {
        assertEquals("en-US", Intents.languageSwitch("клод английский"))
        assertEquals("ru-RU", Intents.languageSwitch("говори русский"))
        assertNull(Intents.languageSwitch("клод покажи логи"))
    }
}
