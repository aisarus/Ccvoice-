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
        assertEquals(Ready.OK, Readiness.verdict(checks()))
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
        assertEquals(R.string.readiness_fix_start, check.fix)
    }

    @Test
    fun batteryOptimisationIsFatalBecauseThePhoneLivesInAPocket() {
        val check = byId(Readiness.BACKGROUND, checks(background = false))
        assertEquals(Ready.BAD, check.state)
        assertEquals(R.string.readiness_background_bad, check.detail)
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
        assertEquals(R.string.readiness_fix_pick, check.fix)
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
        // Итог собирает экран, здесь важно другое: сломанное названо поимённо,
        // а не свалено в одну строку «что-то не так».
        val broken = checks(mic = false, notifications = false)
            .filter { it.state == Ready.BAD }
            .map { it.title }
        assertTrue("$broken", R.string.readiness_mic in broken)
        assertTrue("$broken", R.string.readiness_notifications in broken)
    }

    @Test
    fun aWarningDoesNotPretendEverythingIsFine() {
        assertEquals(Ready.WARN, Readiness.verdict(checks(engine = "")))
    }

    @Test
    fun everyLineSaysBothWhatAndHowBad() {
        // Слова собираются из ресурсов на экране, а здесь проверяется то, что
        // от языка не зависит: знак беды и то, про что она.
        val check = byId(Readiness.MIC, checks(mic = false))
        assertEquals("✗", Readiness.mark(check.state))
        assertEquals(R.string.readiness_mic, check.title)
        assertEquals(R.string.readiness_mic_bad, check.detail)
    }
}
