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

    // -- четыре языка ------------------------------------------------------

    @Test
    fun `обращение по-китайски слышно, хотя пробела после него нет`() {
        // В китайском слова не разделяются пробелом, и `substringBefore(' ')`
        // отдавал бы всю реплику целиком — обращение не находилось никогда.
        assertTrue(Intents.hasWake("克劳德，把测试修一下"))
        assertEquals("把测试修一下", Intents.stripWake("克劳德，把测试修一下"))
    }

    @Test
    fun `стоп понимается на всех четырёх языках`() {
        assertEquals("voice", Intents.stopIntent("стоп"))
        assertEquals("voice", Intents.stopIntent("quiet"))
        assertEquals("voice", Intents.stopIntent("silencio"))
        assertEquals("voice", Intents.stopIntent("安静"))
        assertEquals("work", Intents.stopIntent("останови работу"))
        assertEquals("work", Intents.stopIntent("stop working"))
        assertEquals("work", Intents.stopIntent("detén el trabajo"))
        assertEquals("work", Intents.stopIntent("停止工作"))
    }

    @Test
    fun `язык переключается голосом на любом из четырёх`() {
        assertEquals("es-ES", Intents.languageSwitch("клод, испанский"))
        assertEquals("es-ES", Intents.languageSwitch("claude, spanish"))
        assertEquals("zh-CN", Intents.languageSwitch("claude, chinese"))
        assertEquals("zh-CN", Intents.languageSwitch("克劳德，中文"))
        assertEquals("en-US", Intents.languageSwitch("claude, english"))
        assertEquals("ru-RU", Intents.languageSwitch("клод, русский"))
    }

    @Test
    fun `китайский ответ читается китайским голосом`() {
        assertEquals("zh-CN", Intents.scriptLanguage("已回滚 auth.ts", "en-US"))
    }

    @Test
    fun `испанский от английского письменностью не отличить, решает выбор языка`() {
        // Обе латиницей: гадать по словам здесь незачем — выбранный язык
        // реплик уже говорит, какой из двух имеется в виду.
        assertEquals("es-ES", Intents.scriptLanguage("Anotado.", "es-ES"))
        assertEquals("en-US", Intents.scriptLanguage("Noted.", "en-US"))
        assertEquals("en-US", Intents.scriptLanguage("Noted.", "he-IL"))
    }

    @Test
    fun `откат по-прежнему уезжает к демону, а не гасится телефоном`() {
        // Ради этого STOP_WORK_ALONE и появился: добавление новых языков не
        // должно было вернуть прежнюю поломку.
        assertEquals(null, Intents.stopIntent("отмени последнее"))
        assertEquals(null, Intents.stopIntent("撤销刚才的"))
        assertEquals("work", Intents.stopIntent("отмени"))
    }

    // -- язык ответа отдельно от языка распознавания ------------------------

    @Test
    fun `как спросил снимает закрепление языка ответа`() {
        assertEquals(Intents.ANY_LANGUAGE, Intents.languageSwitch("клод, как спросил"))
        assertEquals(Intents.ANY_LANGUAGE, Intents.languageSwitch("claude, auto"))
        assertEquals(Intents.ANY_LANGUAGE, Intents.languageSwitch("claude, same language"))
    }

    @Test
    fun `о смене языка ответа говорится на нём же`() {
        assertEquals("Answering in English.", Intents.switchNotice("en-US", "ru-RU"))
        assertEquals("Отвечаю по-русски.", Intents.switchNotice("ru-RU", "en-US"))
    }

    @Test
    fun `без закрепления обещание даётся на языке распознавания`() {
        // Закрепления нет — значит отвечаем «как спросили», и сказать об этом
        // надо на том языке, на котором человек сейчас говорит.
        assertEquals("Отвечаю на языке вопроса.", Intents.switchNotice("", "ru-RU"))
        assertEquals("Answering in whatever language you use.",
                     Intents.switchNotice("", "en-US"))
    }

    @Test
    fun `незнакомый язык ответа не роняет обещание в пустоту`() {
        assertEquals("Answering in English.", Intents.switchNotice("fr-FR", "fr-FR"))
    }

    @Test
    fun `слушать все языки и слушать один — разные команды`() {
        assertEquals(Intents.Listen(null, true), Intents.listenSwitch("клод, слушай все языки"))
        assertEquals(Intents.Listen(null, true),
                     Intents.listenSwitch("claude, listen to all languages"))
        assertEquals(null, Intents.listenSwitch("клод, почини тесты"))
    }

    @Test
    fun `слушай только английский — это обе перемены сразу`() {
        // При двух независимых проверках одна всегда съедала бы вторую:
        // «слушай только» срабатывало бы раньше, чем кто-то посмотрит,
        // какой язык назван.
        assertEquals(Intents.Listen("en-US", false),
                     Intents.listenSwitch("клод, слушай только английский"))
        assertEquals(Intents.Listen(null, false), Intents.listenSwitch("клод, слушай только"))
    }

    @Test
    fun `слушай иврит меняет микрофон, а не язык ответа`() {
        assertEquals(Intents.Listen("he-IL", null), Intents.listenSwitch("клод, слушай иврит"))
        assertEquals(Intents.Listen("en-US", null),
                     Intents.listenSwitch("claude, listen in english"))
        // А вот это — язык ответа, и слушать оно ничего не меняет.
        assertEquals(null, Intents.listenSwitch("клод, английский"))
        assertEquals("en-US", Intents.languageSwitch("клод, английский"))
    }

    @Test
    fun `слушать все языки не путается со сменой языка ответа`() {
        // Обе фразы начинаются с обращения и обе про языки: если бы
        // languageSwitch срабатывал первым, «слушай все языки» молча
        // переключал бы язык ответа вместо распознавания.
        assertEquals(null, Intents.languageSwitch("клод, слушай все языки"))
    }

    @Test
    fun `второе ухо открывается и закрывается голосом на четырёх языках`() {
        // Если телефон перестанет узнавать эти фразы, демон всё равно их
        // услышит и ответит «слушаю» — а телефон продолжит молчать в тряпочку
        // и не пришлёт ему ни одной чужой реплики. Ухо будет открыто и глухо.
        for (said in listOf("клод, второе ухо", "второе ухо", "клод, слушай вокруг",
                            "claude, second ear", "claude, listen around",
                            "segundo oído", "第二只耳朵")) {
            assertEquals(said, Intents.SecondEar(true, null), Intents.secondEar(said))
        }
        for (said in listOf("клод, выключи второе ухо", "закрой второе ухо",
                            "claude, turn off the second ear", "no second ear",
                            "apaga el segundo oido", "关掉第二只耳朵")) {
            assertEquals(said, Intents.SecondEar(false, null), Intents.secondEar(said))
        }
    }

    @Test
    fun `хватит слушать вокруг закрывает ухо, а не гасит голос`() {
        // Столкновение было настоящим: фраза начинается со слова, которым
        // гасят голос, и «стоп» разбирается раньше всего остального. Его
        // больше нет — голый глагол теперь ловится только целой репликой, —
        // но проверять стоит обе стороны: и что голос не гаснет, и что ухо
        // закрывается.
        assertNull(Intents.stopIntent("хватит слушать вокруг"))
        assertEquals("voice", Intents.stopIntent("хватит"))
        assertEquals(Intents.SecondEar(false, null), Intents.secondEar("хватит слушать вокруг"))
    }

    @Test
    fun `второе ухо на иврите — это и открыть, и назвать язык комнаты`() {
        // Он говорит по-русски, вокруг говорят на иврите, а распознаватель
        // Android слушает один язык за раз. Требовать здесь две команды
        // значит требовать их посреди чужого разговора.
        assertEquals(Intents.SecondEar(true, "he-IL"), Intents.secondEar("клод, второе ухо на иврите"))
        assertEquals(Intents.SecondEar(true, "he-IL"), Intents.secondEar("claude, second ear in hebrew"))
        assertEquals(Intents.SecondEar(true, "en-US"), Intents.secondEar("второе ухо по-английски"))
    }

    @Test
    fun `разговор о втором ухе его не открывает`() {
        // «А что такое второе ухо?» — вопрос. Открытое по такому вопросу ухо
        // означало бы, что телефон начал слушать чужих без просьбы.
        for (said in listOf("а что такое второе ухо", "расскажи про второе ухо",
                            "what is the second ear")) {
            assertNull(said, Intents.secondEar(said))
        }
    }

    @Test
    fun `слушай вокруг не путается со сменой языка микрофона`() {
        // Обе фразы начинаются с «слушай». Если бы первой срабатывала смена
        // языка, «слушай вокруг» молча ничего не делало бы.
        assertNull(Intents.listenSwitch("клод, слушай вокруг"))
        assertNull(Intents.secondEar("клод, слушай иврит"))
    }

    @Test
    fun `предлог не съедает название языка`() {
        // «Слушай на иврите» не понималось вовсе: открывающая фраза забирала
        // «на», а в списке языков лежит именно «на иврите». Человек говорил
        // как говорится по-русски — и микрофон оставался на прежнем языке.
        assertEquals(Intents.Listen("he-IL", null), Intents.listenSwitch("клод, слушай на иврите"))
        assertEquals(Intents.Listen("ru-RU", null), Intents.listenSwitch("клод, слушай по-русски"))
        assertEquals(Intents.Listen("en-US", null), Intents.listenSwitch("claude, listen in english"))
    }

    // -- то, что нашли разборы кода ----------------------------------------

    @Test
    fun `обращение слышно и после слова-затравки`() {
        // Демон эти слова срезает с самого начала, телефон — нет, и реплика
        // умирала на трубке, не дойдя до того, кто бы её понял.
        assertTrue(Intents.hasWake("эй клод откати последнее"))
        assertTrue(Intents.hasWake("окей клод покажи логи"))
        assertTrue(Intents.hasWake("ну клод давай уже"))
        assertTrue(Intents.hasWake("слушай клод почини тесты"))
        assertTrue(Intents.hasWake("hey claude show the logs"))
        assertTrue(Intents.hasWake("ok claude what's done"))
        assertEquals("откати последнее", Intents.stripWake("эй клод откати последнее"))
    }

    @Test
    fun `cloud — это тоже обращение`() {
        // Самое частое, что слышит английский распознаватель вместо Claude.
        // Допуск в одну букву не спасал: там две замены.
        assertTrue(Intents.hasWake("cloud show me the logs"))
        assertTrue(Intents.hasWake("clode fix the tests"))
    }

    @Test
    fun `второе ухо выключи — это просьба закрыть, а не открыть`() {
        // Совпадало начало фразы, и просьба закрыть ОТКРЫВАЛА микрофон на
        // комнату — ровно то, чего нельзя делать без прямой просьбы.
        assertEquals(Intents.SecondEar(false, null), Intents.secondEar("второе ухо выключи"))
        assertEquals(Intents.SecondEar(false, null), Intents.secondEar("второе ухо убери"))
        assertEquals(Intents.SecondEar(false, null), Intents.secondEar("второе ухо не надо"))
        assertEquals(Intents.SecondEar(false, null), Intents.secondEar("second ear off"))
    }

    @Test
    fun `разговор про второе ухо микрофон не открывает`() {
        assertNull(Intents.secondEar("второе ухо это метафора"))
        assertNull(Intents.secondEar("второе ухо это что"))
        assertNull(Intents.secondEar("second ear in the code is called secondear"))
        // А сама команда и команда с языком — открывают.
        assertEquals(Intents.SecondEar(true, null), Intents.secondEar("второе ухо"))
        assertEquals(Intents.SecondEar(true, "he-IL"), Intents.secondEar("второе ухо на иврите"))
    }

    @Test
    fun `фраза про язык — не команда сменить язык`() {
        // Команды смены языка у демона нет: перехваченная реплика не доходит
        // никуда вообще, и человек этого не видит.
        assertNull(Intents.languageSwitch("клод английский текст в логах не переводи"))
        assertNull(Intents.languageSwitch("клод русский язык в интерфейсе надо поправить"))
        assertNull(Intents.languageSwitch("клод автоматически запускай тесты после сборки"))
        assertNull(Intents.languageSwitch("claude auto format the code"))
        assertNull(Intents.languageSwitch("claude english is the default locale"))
        // Сама команда работать не перестала.
        assertEquals("en-US", Intents.languageSwitch("клод английский"))
        assertEquals("en-US", Intents.languageSwitch("клод переключись на английский"))
        assertEquals(Intents.ANY_LANGUAGE, Intents.languageSwitch("клод как спросил"))
    }

    @Test
    fun `останови сервер — это поручение, а не остановка работы`() {
        // Голый глагол съедал обычные команды: «останови сервер» гасил работу
        // Claude вместо того, чтобы её ему поручить.
        assertNull(Intents.stopIntent("клод останови сервер"))
        assertNull(Intents.stopIntent("клод прекрати сыпать логи"))
        assertNull(Intents.stopIntent("claude stop the server"))
        assertNull(Intents.stopIntent("claude abort the migration if it hangs"))
        assertNull(Intents.stopIntent("claude enough of the retries use a queue"))
        assertNull(Intents.stopIntent("para el informe necesito los logs"))
        // Голое слово по-прежнему останавливает.
        assertEquals("voice", Intents.stopIntent("стоп"))
        assertEquals("voice", Intents.stopIntent("тихо"))
        assertEquals("work", Intents.stopIntent("останови"))
        assertEquals("work", Intents.stopIntent("останови работу"))
    }

    @Test
    fun `отмени задачу доезжает до демона, а не гасит работу`() {
        // «cancel the task» — фраза, которой у демона отменяют фоновую
        // задачу. Телефон превращал её в прерывание текущей работы: два
        // разных разрушительных действия.
        assertNull(Intents.stopIntent("cancel the task about dependencies"))
        assertNull(Intents.stopIntent("cancela la tarea"))
        assertNull(Intents.stopIntent("отмени задачу про тесты"))
    }

    @Test
    fun `слушай только ошибки — не про языки`() {
        // Такая фраза выключала многоязычие насовсем: переключателя на экране
        // нет, а команда, которая вернула бы его, живёт на том же пути.
        assertNull(Intents.listenSwitch("клод слушай только ошибки"))
        assertNull(Intents.listenSwitch("claude listen only to stderr"))
        assertEquals(Intents.Listen("en-US", false),
                     Intents.listenSwitch("клод слушай только английский"))
        assertEquals(Intents.Listen(null, false), Intents.listenSwitch("клод слушай только"))
    }
}
