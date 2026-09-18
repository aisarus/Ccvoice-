package com.voiceshell

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.Looper

/**
 * Куда смотрит микрофон.
 *
 * Проводная гарнитура подключается сама: система отдаёт её микрофон всем, кто
 * пишет звук. Bluetooth — нет. Микрофон гарнитуры живёт на отдельном канале
 * (SCO, в новых системах — через выбор устройства связи), и пока этот канал не
 * поднят, приложение продолжает слушать встроенный микрофон телефона. Со
 * стороны это выглядит как «гарнитура надета, а он не слышит».
 *
 * За канал приходится платить: он монофонический и узкополосный, поэтому голос
 * синтеза звучит как в телефонном разговоре, а гарнитура считает, что идёт
 * звонок, и тратит на это батарею. Поэтому канал поднимается, только когда
 * гарнитура действительно подключена, и его можно выключить в настройках.
 *
 * Отдельная тонкость: уже открытый поток записи не переезжает на новое
 * устройство сам. Поэтому о каждой смене маршрута класс сообщает наружу —
 * тот, кто пишет звук, должен перезапуститься.
 */
class AudioRoute(
    private val context: Context,
    private val onChanged: (String) -> Unit,
    private val log: (String) -> Unit,
) {

    companion object {
        /** AudioDeviceInfo.TYPE_BLE_HEADSET — появился в Android 12. */
        private const val TYPE_BLE_HEADSET = 26
        private const val RETRY_MS = 2_000L
        private const val SETTLE_MS = 400L
    }

    private val audio: AudioManager = context.getSystemService(AudioManager::class.java)
    private val main = Handler(Looper.getMainLooper())

    private var wanted = false
    private var engaged = false
    private var watching = false
    private var previousMode = AudioManager.MODE_NORMAL

    /** Слышим ли мы сейчас через микрофон гарнитуры. */
    val onBluetoothMic: Boolean get() = engaged

    /** Человеческая строка для журнала: на телефоне это единственная диагностика. */
    fun describe(): String = context.getString(
        when {
            engaged -> R.string.mic_headset_bluetooth
            !wanted -> R.string.mic_phone_bluetooth_off
            bluetoothMicPresent() -> R.string.mic_phone_no_channel
            else -> R.string.mic_phone
        }
    )

    fun start(enabled: Boolean) {
        wanted = enabled
        watch()
        engage("start")
    }

    /** Переключатель в приложении: гарнитура может вести себя плохо. */
    fun enable(enabled: Boolean) {
        if (wanted == enabled) return
        wanted = enabled
        if (enabled) engage("headset mic enabled")
        else disengage("headset mic disabled")
    }

    fun release() {
        wanted = false
        unwatch()
        main.removeCallbacksAndMessages(null)
        disengage("stop", notify = false)
    }

    // ---------- слежение за устройствами ----------
    private val deviceCallback = object : AudioDeviceCallback() {
        override fun onAudioDevicesAdded(added: Array<out AudioDeviceInfo>?) {
            main.post { engage("headset connected") }
        }

        override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>?) {
            main.post {
                if (engaged && !bluetoothMicPresent()) disengage("headset disconnected")
            }
        }
    }

    /** Старый путь сообщает о готовности канала только широковещанием. */
    private val scoReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            when (intent?.getIntExtra(AudioManager.EXTRA_SCO_AUDIO_STATE, -1)) {
                AudioManager.SCO_AUDIO_STATE_CONNECTED -> ready("headset channel up")
                AudioManager.SCO_AUDIO_STATE_DISCONNECTED -> if (engaged) {
                    engaged = false
                    log(describe())
                    onChanged("headset channel dropped")
                    // Система роняет канал и сама по себе — пробуем ещё раз.
                    main.postDelayed({ engage("reconnect") }, RETRY_MS)
                }
            }
        }
    }

    private fun watch() {
        if (watching) return
        watching = true
        runCatching { audio.registerAudioDeviceCallback(deviceCallback, main) }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
            runCatching {
                context.registerReceiver(
                    scoReceiver, IntentFilter(AudioManager.ACTION_SCO_AUDIO_STATE_UPDATED)
                )
            }
        }
    }

    private fun unwatch() {
        if (!watching) return
        watching = false
        runCatching { audio.unregisterAudioDeviceCallback(deviceCallback) }
        runCatching { context.unregisterReceiver(scoReceiver) }
    }

    // ---------- сам маршрут ----------
    @Suppress("DEPRECATION")   // startBluetoothSco — путь для систем до Android 12
    private fun engage(why: String) {
        if (!wanted || engaged) return
        if (!bluetoothMicPresent()) return

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val device = runCatching {
                audio.availableCommunicationDevices.firstOrNull { isBluetoothMic(it) }
            }.getOrNull() ?: return
            val ok = runCatching {
                takeCommunicationMode()
                audio.setCommunicationDevice(device)
            }.getOrDefault(false)
            if (ok) {
                // Устройство выбрано, но канал открывается не мгновенно.
                main.postDelayed({ ready(why) }, SETTLE_MS)
            } else {
                restoreMode()
                log(context.getString(R.string.mic_switch_failed))
            }
            return
        }

        if (!audio.isBluetoothScoAvailableOffCall) {
            log(context.getString(R.string.mic_sco_unavailable))
            return
        }
        runCatching {
            takeCommunicationMode()
            audio.startBluetoothSco()
            audio.isBluetoothScoOn = true
        }.onFailure {
            restoreMode()
            log(context.getString(R.string.mic_channel_failed, it.javaClass.simpleName))
        }
        // Дальше ждём SCO_AUDIO_STATE_CONNECTED: раньше слушать нечего.
    }

    /**
     * Канал поднялся — если он и правда поднялся.
     *
     * На Android 12+ сюда приходили вслепую, через паузу после выбора
     * устройства, и ничего не перепроверяли. Гарнитура, отвалившаяся за эти
     * миллисекунды, оставляла «занято гарнитурой» защёлкнутым навсегда:
     * `engage` дальше выходил сразу, а `disengage` приходит только с
     * событием отключения, которого уже не будет. Всё остальное следовало
     * за этим враньём — речь и сигналы уходили в канал связи, которого нет,
     * то есть в тишину, а реплики метились как узкополосные.
     */
    private fun ready(why: String) {
        if (engaged || !wanted) return
        if (!bluetoothMicPresent()) {
            restoreMode()
            log(context.getString(R.string.mic_switch_failed))
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val live = runCatching {
                audio.communicationDevice?.let { isBluetoothMic(it) }
            }.getOrNull()
            if (live != true) {
                restoreMode()
                log(context.getString(R.string.mic_switch_failed))
                return
            }
        }
        engaged = true
        log(describe())
        onChanged(why)
    }

    @Suppress("DEPRECATION")
    private fun disengage(why: String, notify: Boolean = true) {
        val was = engaged
        engaged = false
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                audio.clearCommunicationDevice()
            } else {
                audio.isBluetoothScoOn = false
                audio.stopBluetoothSco()
            }
        }
        restoreMode()
        if (was && notify) {
            log(describe())
            onChanged(why)
        }
    }

    /**
     * Канал гарнитуры открывается надёжно только когда система считает, что у
     * приложения идёт разговор. Прежний режим возвращаем, как только канал не
     * нужен, чтобы не оставить телефон «в звонке».
     */
    private fun takeCommunicationMode() {
        if (audio.mode == AudioManager.MODE_IN_COMMUNICATION) return
        previousMode = audio.mode
        audio.mode = AudioManager.MODE_IN_COMMUNICATION
    }

    private fun restoreMode() {
        runCatching {
            if (audio.mode == AudioManager.MODE_IN_COMMUNICATION) audio.mode = previousMode
        }
    }

    /**
     * Есть ли вообще гарнитура с микрофоном.
     *
     * Смотрим три списка нарочно: часть телефонов показывает микрофон
     * гарнитуры среди входов только после того, как канал уже поднят, —
     * и если верить только входам, канал не поднимется никогда.
     */
    private fun bluetoothMicPresent(): Boolean = runCatching {
        val inputs = audio.getDevices(AudioManager.GET_DEVICES_INPUTS).any { isBluetoothMic(it) }
        val outputs = audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS).any { isBluetoothMic(it) }
        val comms = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            audio.availableCommunicationDevices.any { isBluetoothMic(it) }
        inputs || outputs || comms
    }.getOrDefault(false)

    private fun isBluetoothMic(device: AudioDeviceInfo): Boolean =
        device.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO ||
            (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && device.type == TYPE_BLE_HEADSET)
}
