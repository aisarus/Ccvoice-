package com.voiceshell

import org.junit.Assert.assertFalse
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
        // «Крот» — одна буква от «клот», но две от «Клод». Допуск считается
        // от настоящих форм обращения, иначе ловушка расходится вдвое.
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
}
