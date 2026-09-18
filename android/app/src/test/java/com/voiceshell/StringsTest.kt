package com.voiceshell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

/**
 * Сторож четырёх языков.
 *
 * Ключ, который забыли перевести, виден только тому, кто открыл приложение на
 * этом языке; лишняя подстановка в переводе роняет экран уже в руках у
 * человека, и не при сборке, а при показе строки. Ни то, ни другое компилятор
 * не ловит, поэтому ловим здесь: тест читает сами файлы ресурсов и код рядом с
 * ними, без эмулятора и без Android SDK.
 */
class StringsTest {

    private val module: File =
        generateSequence(File("").absoluteFile) { it.parentFile }
            .firstOrNull { File(it, "src/main/res/values/strings.xml").isFile }
            ?: throw AssertionError("не нашёл каталог модуля с ресурсами")

    private val res = File(module, "src/main/res")

    /** Английский — основной, остальные сверяются с ним. */
    private val translations = listOf("values-ru", "values-es", "values-zh")

    private class Strings(
        val values: Map<String, String>,
        val untranslatable: Set<String>,
    )

    private fun read(dir: String): Strings {
        val file = File(res, "$dir/strings.xml")
        assertTrue("нет файла $file", file.isFile)
        val document = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(file)
        val nodes = document.getElementsByTagName("string")
        val values = LinkedHashMap<String, String>()
        val untranslatable = mutableSetOf<String>()
        for (i in 0 until nodes.length) {
            val element = nodes.item(i) as Element
            val name = element.getAttribute("name")
            val already = values.put(name, element.textContent)
            assertTrue("$dir: ключ $name объявлен дважды", already == null)
            if (element.getAttribute("translatable") == "false") untranslatable += name
        }
        return Strings(values, untranslatable)
    }

    private val base = read("values")

    /** Всё, на что ссылается код, разметка и манифест. */
    private fun referenced(): Set<String> {
        val keys = mutableSetOf<String>()
        val fromCode = Regex("R\\.string\\.([a-z0-9_]+)")
        val fromXml = Regex("@string/([a-z0-9_]+)")
        File(module, "src/main/java").walkTopDown()
            .filter { it.isFile && it.extension == "kt" }
            .forEach { file -> fromCode.findAll(file.readText()).forEach { keys += it.groupValues[1] } }
        (File(res, "layout").walkTopDown().filter { it.isFile && it.extension == "xml" } +
            sequenceOf(File(module, "src/main/AndroidManifest.xml")))
            .forEach { file -> fromXml.findAll(file.readText()).forEach { keys += it.groupValues[1] } }
        return keys
    }

    /** «%1$s», «%2$d» — в переводе их должно быть ровно столько же и тех же. */
    private fun placeholders(text: String): List<String> =
        Regex("%\\d+\\\$[a-zA-Z]").findAll(text).map { it.value }.sorted().toList()

    @Test
    fun everyKeyTheAppAsksForExists() {
        val missing = referenced().filterNot { it in base.values.keys }
        assertEquals("нет в values/strings.xml: $missing", emptyList<String>(), missing.sorted())
    }

    @Test
    fun theLanguagesArrayTheScreenReadsExists() {
        val file = File(res, "values/strings.xml").readText()
        assertTrue(
            "пропал string-array с названиями языков реплик",
            file.contains("<string-array name=\"reply_language_names\"")
        )
    }

    @Test
    fun everyTranslatableStringIsTranslatedEverywhere() {
        val wanted = base.values.keys - base.untranslatable
        for (dir in translations) {
            val missing = (wanted - read(dir).values.keys).sorted()
            assertEquals("$dir: не переведено", emptyList<String>(), missing)
        }
    }

    @Test
    fun noTranslationInventsKeysOfItsOwn() {
        for (dir in translations) {
            val strings = read(dir)
            val stray = (strings.values.keys - base.values.keys).sorted()
            assertEquals("$dir: ключа нет в основном языке", emptyList<String>(), stray)
            // Имя приложения и склейки вида «%1$s (%2$s)» переводить нечего:
            // перевод такого ключа — ошибка сборки, а не находка.
            val forbidden = strings.values.keys.filter { it in base.untranslatable }.sorted()
            assertEquals("$dir: переведено непереводимое", emptyList<String>(), forbidden)
        }
    }

    @Test
    fun thePlaceholdersMatchTheBaseLanguage() {
        for (dir in translations) {
            val strings = read(dir)
            for ((key, text) in strings.values) {
                // Лишние ключи ловит соседний тест, здесь они только помешали бы.
                if (key !in base.values.keys) continue
                val wanted = placeholders(base.values.getValue(key))
                assertEquals("$dir/$key: подстановки разошлись", wanted, placeholders(text))
            }
        }
    }

    @Test
    fun nothingIsLeftEmpty() {
        for (dir in listOf("values") + translations) {
            val blank = read(dir).values.filterValues { it.isBlank() }.keys.sorted()
            assertEquals("$dir: пустая строка", emptyList<String>(), blank)
        }
    }

    @Test
    fun theLinkStatesNeverShareAWording() {
        // «Нет связи» на все беды сразу — ровно то, из-за чего человек чинил не
        // то. В русском состояния однажды развели; на переводе их так же легко
        // свести обратно одним удобным словом.
        val labels = listOf(
            "link_unknown", "link_off", "link_connecting", "link_online",
            "link_no_network", "link_no_server", "link_refused", "link_cleartext",
        )
        val hints = listOf(
            "link_hint_off", "link_hint_unknown", "link_hint_connecting", "link_hint_online",
            "link_hint_no_network", "link_hint_no_server", "link_hint_refused",
            "link_hint_cleartext",
        )
        for (dir in listOf("values") + translations) {
            val strings = read(dir).values
            for (group in listOf(labels, hints)) {
                val said = group.map { strings.getValue(it) }
                assertEquals(
                    "$dir: одни и те же слова на разные состояния связи",
                    group.size, said.toSet().size
                )
            }
        }
    }
}
