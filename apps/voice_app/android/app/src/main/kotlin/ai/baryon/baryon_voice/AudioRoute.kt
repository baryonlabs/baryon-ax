package ai.baryon.baryon_voice

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.telecom.CallAudioState
import android.telecom.CallEndpoint

/** Where the secretary's voice comes out, reported to the screen as "route" events. */
object AudioRoute {
    fun name(endpointType: Int) = when (endpointType) {
        CallEndpoint.TYPE_SPEAKER -> "speaker"
        CallEndpoint.TYPE_BLUETOOTH -> "bluetooth"
        CallEndpoint.TYPE_WIRED_HEADSET -> "wired"
        CallEndpoint.TYPE_STREAMING -> "streaming"
        else -> "earpiece"
    }

    fun legacyName(route: Int) = when (route) {
        CallAudioState.ROUTE_SPEAKER -> "speaker"
        CallAudioState.ROUTE_BLUETOOTH -> "bluetooth"
        CallAudioState.ROUTE_WIRED_HEADSET -> "wired"
        else -> "earpiece"
    }

    fun current(route: String) = VoiceEngine.emit("route", "route" to route)

    fun report(available: List<String>) = VoiceEngine.emit("routes", "available" to org.json.JSONArray(available))

    /** Without a Telecom call (it refused us), switch the communication device directly. */
    fun fallbackSpeaker(context: Context, on: Boolean) {
        val audio = context.getSystemService(AudioManager::class.java) ?: return
        if (Build.VERSION.SDK_INT >= 31) {
            if (on) {
                audio.availableCommunicationDevices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
                    ?.let { audio.setCommunicationDevice(it) }
            } else {
                audio.clearCommunicationDevice()
            }
        } else {
            @Suppress("DEPRECATION")
            audio.isSpeakerphoneOn = on
        }
        current(if (on) "speaker" else "earpiece")
    }
}
