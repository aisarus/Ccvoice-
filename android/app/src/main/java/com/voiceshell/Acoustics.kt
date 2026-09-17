package com.voiceshell

/**
 * Один измеренный речевой сегмент — в тех единицах, в которых их отдаёт
 * распознаватель телефона. Это не дБ полной шкалы: у каждого движка своя
 * шкала, и привязки к абсолютным децибелам у неё нет.
 */
data class Segment(
    /** Медиана громких кадров. */
    val speech: Double,
    /** Медиана тихих кадров — фон комнаты. */
    val noise: Double,
    /** Сколько миллисекунд в сегменте было речи. */
    val voicedMs: Int,
    /** Длина речевого сегмента целиком, как её разметил распознаватель. */
    val durationMs: Int,
)

/** Норма конкретного микрофона: как он слышит хозяина, когда всё хорошо. */
data class Baseline(val speech: Double, val snr: Double)

/**
 * Акустические признаки реплики.
 *
 * Главная идея спеки — отличать речь хозяина от чужой речи рядом — на телефоне
 * не работала вовсе: приложение объявляло себя `master` без единого измерения,
 * и вся защита от чужого голоса существовала только на бумаге. Измерить здесь
 * можно немного: к самому звуку доступа нет, распознаватель отдаёт лишь череду
 * значений RMS в onRmsChanged. Поэтому считаются два признака из шести, а
 * остальные не посылаются вовсе — демон обнуляет вес того, чего нет, и это
 * честнее выдуманного числа.
 */
object Acoustics {

    /** Меньше этого числа кадров — медианы ничего не значат. */
    const val MIN_FRAMES = 8

    /** Кадров у распознавателя бывает много; держать все незачем. */
    const val MAX_FRAMES = 600

    /** Меньший размах громкости — это не речь, а ровный шум. */
    const val MIN_CONTRAST = 1.5

    /**
     * Кадр 10 мс.
     *
     * Своих кадров распознаватель не показывает, поэтому речевое время
     * переводится в кадры. Берём короткий кадр нарочно: при 10 мс жёсткий гейт
     * демона «меньше 12 кадров» (120 мс) мягче гейта «короче 400 мс», и
     * короткое «да» не уезжает в unknown из-за одной только арифметики.
     */
    const val FRAME_MS = 10

    /**
     * Типичное отношение сигнал/шум близкой речи, в дБ.
     *
     * Шкала onRmsChanged своя у каждого движка и к дБ не привязана, так что
     * абсолютных децибел телефон не знает. Зато он знает собственную норму:
     * сколько получается у этого микрофона, когда говорит хозяин. Норму и
     * приравниваем к этому числу — тому же, которое демон считает нормой
     * мастера. Иначе в демон уходили бы сырые единицы движка (размах у них
     * порядка десяти), и мастер уверенно классифицировался бы как сосед.
     */
    const val TYPICAL_MASTER_SNR_DB = 26.0

    /** Вверх норма идёт заметно: хозяин заговорил громче — это новая норма. */
    private const val UP = 0.25

    /** Вниз — еле-еле, иначе одна тихая реплика сделала бы тихое нормальным. */
    private const val DOWN = 0.05

    /** Насколько тише нормы реплика ещё может считаться репликой хозяина. */
    private const val DROP_LIMIT = 3.0

    /**
     * Признаки, которые можно честно назвать измеренными.
     *
     * Пока нормы микрофона нет, сравнивать не с чем: возвращаем пустую карту, и
     * реплика уходит вообще без признаков. Первая реплика на новом микрофоне
     * норму и задаёт — так же, как описано в спеке для работы без калибровки.
     */
    fun features(segment: Segment, baseline: Baseline?): Map<String, Double> {
        if (baseline == null) return emptyMap()
        val scale = scale(baseline)
        val snr = ((segment.speech - segment.noise) * scale).coerceIn(0.0, 45.0)
        val level = ((segment.speech - baseline.speech) * scale).coerceIn(-40.0, 12.0)
        return mapOf(
            "level_rel_db" to round1(level),
            "snr_db" to round1(snr),
        )
    }

    /** Сколько речевых кадров получилось — для жёстких гейтов демона. */
    fun voicedFrames(segment: Segment): Int = segment.voicedMs / FRAME_MS

    /**
     * Новая норма микрофона.
     *
     * Несимметрично нарочно. Если бы норма шла за каждой репликой, одна тихая
     * фраза опустила бы её, следующая такая же выглядела бы нормальной — и
     * различать своё и чужое стало бы нечем. Поэтому вверх норма идёт охотно,
     * вниз — по чуть-чуть, а совсем далёкая речь её не трогает вовсе.
     */
    fun nextBaseline(segment: Segment, baseline: Baseline?): Baseline {
        val snr = segment.speech - segment.noise
        if (baseline == null) return Baseline(segment.speech, snr)
        return Baseline(follow(baseline.speech, segment.speech), follow(baseline.snr, snr))
    }

    /** Во сколько раз шкала движка мельче настоящих децибел. */
    fun scale(baseline: Baseline): Double =
        if (baseline.snr < 0.5) 1.0
        else (TYPICAL_MASTER_SNR_DB / baseline.snr).coerceIn(0.5, 6.0)

    private fun follow(base: Double, now: Double): Double = when {
        now > base -> base + UP * (now - base)
        now > base - DROP_LIMIT -> base + DOWN * (now - base)
        else -> base
    }

    private fun round1(value: Double): Double = Math.round(value * 10.0) / 10.0

    /** Медиана уже отсортированного куска. */
    internal fun median(sorted: List<Float>): Double {
        if (sorted.isEmpty()) return 0.0
        val middle = sorted.size / 2
        return if (sorted.size % 2 == 1) sorted[middle].toDouble()
        else (sorted[middle - 1].toDouble() + sorted[middle].toDouble()) / 2.0
    }
}

/**
 * Копилка RMS за одну реплику.
 *
 * Распознаватель зовёт onRmsChanged с самого открытия микрофона, поэтому в
 * копилке оказывается и тишина перед репликой — из неё и берётся фон. Класс
 * намеренно ничего не знает про Android: так его поведение проверяется на JVM
 * за секунды, без эмулятора.
 */
class SpeechMeter {

    private val rms = ArrayList<Float>(128)
    private var firstAt = 0L
    private var lastAt = 0L
    private var speechFrom = 0L
    private var speechTo = 0L

    fun reset() {
        rms.clear()
        firstAt = 0L
        lastAt = 0L
        speechFrom = 0L
        speechTo = 0L
    }

    fun frame(value: Float, atMs: Long) {
        // Часть движков отдаёт на старте NaN, и одна такая величина испортила бы
        // всю сортировку: NaN в сравнениях больше любого числа.
        if (!value.isFinite()) return
        if (rms.size >= Acoustics.MAX_FRAMES) return
        if (rms.isEmpty()) firstAt = atMs
        lastAt = atMs
        rms.add(value)
    }

    fun speechBegan(atMs: Long) {
        if (speechFrom == 0L) speechFrom = atMs
    }

    fun speechEnded(atMs: Long) {
        speechTo = atMs
    }

    /** Что удалось измерить, или null — если мерить нечего. */
    fun segment(): Segment? {
        if (rms.size < Acoustics.MIN_FRAMES) return null
        val sorted = rms.sorted()
        val take = maxOf(2, sorted.size * 3 / 10)
        val noise = Acoustics.median(sorted.subList(0, take))
        val speech = Acoustics.median(sorted.subList(sorted.size - take, sorted.size))
        // Ровная дорожка без всплесков — это шум, а не речь: признаки по ней
        // были бы выдумкой.
        if (speech - noise < Acoustics.MIN_CONTRAST) return null

        val gate = (speech + noise) / 2.0
        val loud = rms.count { it > gate }
        // Частота вызовов у каждого движка своя — меряем её, а не угадываем.
        val frameMs = if (rms.size > 1) (lastAt - firstAt).toDouble() / (rms.size - 1) else 0.0
        if (frameMs <= 0.0 || frameMs > 500.0) return null

        val voicedMs = (loud * frameMs).toInt()
        val duration = if (speechFrom > 0L && speechTo > speechFrom) (speechTo - speechFrom).toInt()
        else voicedMs
        return Segment(speech, noise, voicedMs, duration)
    }
}
