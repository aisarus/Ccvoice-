package com.voiceshell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Знак ударения, попавший в чужой движок, читается вслух как «плюс» —
 * это хуже, чем неверное ударение. Поэтому проверяем каждый исход.
 */
class StressTest {

    private val marked = "Откат+ил конф+иг, т+есты зелёные."

    @Test
    fun rhvoiceKeepsTheMarks() {
        assertEquals(marked, Stress.render(marked, Stress.AUTO, "com.github.olga_yakovleva.rhvoice.android"))
    }

    @Test
    fun anyOtherEngineNeverHearsAPlus() {
        val spoken = Stress.render(marked, Stress.AUTO, "com.google.android.tts")
        assertEquals("Откатил конфиг, тесты зелёные.", spoken)
        assertTrue(!spoken.contains('+'))
    }

    @Test
    fun theAcuteStyleMovesTheMarkAfterTheVowel() {
        assertEquals("Откати́л конфи́г, те́сты зелёные.",
                     Stress.render(marked, Stress.ACUTE_STYLE, "любой"))
    }

    @Test
    fun switchingOffLeavesPlainText() {
        assertEquals("Откатил конфиг, тесты зелёные.",
                     Stress.render(marked, Stress.OFF, "com.github.olga_yakovleva.rhvoice.android"))
    }

    @Test
    fun aLonePlusIsNotAStressMark() {
        // «2 + 2» произносится как есть: знак ударения — только перед гласной.
        assertEquals("2 + 2", Stress.acute("2 + 2"))
    }

    @Test
    fun theStyleCyclesThroughAllFour() {
        val seen = mutableListOf(Stress.AUTO)
        repeat(3) { seen += Stress.next(seen.last()) }
        assertEquals(Stress.STYLES, seen)
        assertEquals(Stress.AUTO, Stress.next(seen.last()))
    }
}
