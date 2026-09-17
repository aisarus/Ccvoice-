package com.voiceshell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * «Не работает» выглядит одинаково при выключенных уведомлениях, спящей службе
 * и незапущенном демоне. Проверка готовности существует ровно для того, чтобы
 * человек видел, что именно сломано, и чинил одним нажатием.
 */
class ReadinessTest {

    private val engines = listOf("com.google.android.tts", "com.github.olga_yakovleva.rhvoice.android")

    private fun checks(
        mic: Boolean = true,
        notifications: Boolean = true,
        background: Boolean = true,
        engine: String = "com.github.olga_yakovleva.rhvoice.android",
        installed: List<String> = engines,
        link: LinkState = LinkState.ONLINE,
    ) = Readiness.checks(mic, notifications, background, engine, installed, link)

    private fun byId(id: String, checks: List<Check>) = checks.first { it.id == id }

    @Test
    fun everythingInPlaceReadsAsReady() {
        assertEquals("всё готово", Readiness.summary(checks()))
    }

    @Test
    fun aMissingMicrophonePermissionIsFatalAndFixable() {
        val check = byId(Readiness.MIC, checks(mic = false))
        assertEquals(Ready.BAD, check.state)
        assertNotNull("без кнопки человеку некуда нажать", check.fix)
    }

    @Test
    fun theSleepingServiceIsNamedNotGuessedAt() {
        // Самая частая беда: разрешения выданы, а служба не запущена — и
        // приложение молчит без единого слова о том, почему.
        val check = byId(Readiness.LINK, checks(link = LinkState.UNKNOWN))
        assertEquals(Ready.WARN, check.state)
        assertEquals("запустить", check.fix)
    }

    @Test
    fun batteryOptimisationIsFatalBecauseThePhoneLivesInAPocket() {
        val check = byId(Readiness.BACKGROUND, checks(background = false))
        assertEquals(Ready.BAD, check.state)
        assertTrue(check.detail.contains("усыпит"))
    }

    @Test
    fun theDefaultRobotIsAWarningWhileNoEngineAtAllIsAFailure() {
        assertEquals(Ready.WARN, byId(Readiness.VOICE, checks(engine = "")).state)
        assertEquals(Ready.BAD, byId(Readiness.VOICE, checks(installed = emptyList())).state)
    }

    @Test
    fun anEngineThatWasUninstalledIsCaughtBeforeItGoesSilent() {
        // RHVoice удалили, а выбор остался: раньше это выяснялось только тем,
        // что телефон переставал говорить.
        val check = byId(Readiness.VOICE, checks(engine = "com.gone.tts"))
        assertEquals(Ready.BAD, check.state)
        assertEquals("выбрать", check.fix)
    }

    @Test
    fun aConnectedDaemonIsTheOnlyGreenLink() {
        for (state in LinkState.values()) {
            val check = byId(Readiness.LINK, checks(link = state))
            val green = check.state == Ready.OK
            assertEquals(state.name, state == LinkState.ONLINE, green)
        }
    }

    @Test
    fun anUnconfiguredLinkHasNothingToPressBecauseTheFieldsAreRightThere() {
        val check = byId(Readiness.LINK, checks(link = LinkState.OFF))
        assertEquals(Ready.BAD, check.state)
        assertNull(check.fix)
    }

    @Test
    fun theSummaryNamesWhatIsBroken() {
        val summary = Readiness.summary(checks(mic = false, notifications = false))
        assertTrue(summary, summary.contains("микрофон"))
        assertTrue(summary, summary.contains("уведомления"))
    }

    @Test
    fun aWarningDoesNotPretendEverythingIsFine() {
        assertEquals("готово, но есть замечания", Readiness.summary(checks(engine = "")))
    }

    @Test
    fun everyLineSaysBothWhatAndHowBad() {
        val line = Readiness.line(byId(Readiness.MIC, checks(mic = false)))
        assertTrue(line, line.startsWith("✗"))
        assertTrue(line, line.contains("микрофон"))
    }
}
