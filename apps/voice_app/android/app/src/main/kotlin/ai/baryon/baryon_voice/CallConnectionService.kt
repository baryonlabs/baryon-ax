package ai.baryon.baryon_voice

import android.content.ComponentName
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.OutcomeReceiver
import android.telecom.CallAudioState
import android.telecom.CallEndpoint
import android.telecom.CallEndpointException
import android.telecom.Connection
import android.telecom.ConnectionRequest
import android.telecom.ConnectionService
import android.telecom.DisconnectCause
import android.telecom.PhoneAccount
import android.telecom.PhoneAccountHandle
import android.telecom.TelecomManager
import android.util.Log

/**
 * Registers the secretary call with Android's call system (a self-managed connection).
 * That is what makes the car treat it as a phone call: audio over Bluetooth hands-free,
 * the call on the car display, and steering-wheel buttons to hang up.
 */
class CallConnectionService : ConnectionService() {
    override fun onCreateOutgoingConnection(account: PhoneAccountHandle?, request: ConnectionRequest?): Connection {
        val connection = SecretaryConnection(applicationContext)
        val extras = request?.extras ?: Bundle()
        connection.setAddress(request?.address ?: Uri.fromParts("sip", "secretary", null), TelecomManager.PRESENTATION_ALLOWED)
        connection.setCallerDisplayName(applicationContext.getString(R.string.secretary_name), TelecomManager.PRESENTATION_ALLOWED)
        connection.connectionProperties = Connection.PROPERTY_SELF_MANAGED
        connection.audioModeIsVoip = true
        connection.setInitializing()
        current = connection
        VoiceCallService.start(applicationContext, extras.getString(EXTRA_URL) ?: "", extras.getString(EXTRA_TOKEN) ?: "")
        connection.setActive()
        return connection
    }

    override fun onCreateOutgoingConnectionFailed(account: PhoneAccountHandle?, request: ConnectionRequest?) {
        Log.w(TAG, "telecom refused the call; starting without it")
        val extras = request?.extras ?: Bundle()
        VoiceCallService.start(applicationContext, extras.getString(EXTRA_URL) ?: "", extras.getString(EXTRA_TOKEN) ?: "")
    }

    class SecretaryConnection(private val context: Context) : Connection() {
        private var endpoints: List<CallEndpoint> = emptyList()

        override fun onAvailableCallEndpointsChanged(available: MutableList<CallEndpoint>) {
            endpoints = available.toList()
            AudioRoute.report(available.map { AudioRoute.name(it.endpointType) })
        }

        override fun onCallEndpointChanged(endpoint: CallEndpoint) {
            AudioRoute.current(AudioRoute.name(endpoint.endpointType))
        }

        @Deprecated("Android 13 and older")
        override fun onCallAudioStateChanged(state: CallAudioState?) {
            if (Build.VERSION.SDK_INT >= 34 || state == null) return
            AudioRoute.current(AudioRoute.legacyName(state.route))
        }

        /** Speaker on, or back to the best private route: Bluetooth (the car), a headset, then the earpiece. */
        fun setSpeaker(on: Boolean) {
            if (Build.VERSION.SDK_INT >= 34 && endpoints.isNotEmpty()) {
                val order = if (on) listOf(CallEndpoint.TYPE_SPEAKER)
                else listOf(CallEndpoint.TYPE_BLUETOOTH, CallEndpoint.TYPE_WIRED_HEADSET, CallEndpoint.TYPE_EARPIECE)
                val target = order.firstNotNullOfOrNull { type -> endpoints.firstOrNull { it.endpointType == type } } ?: return
                requestCallEndpointChange(target, context.mainExecutor, object : OutcomeReceiver<Void, CallEndpointException> {
                    override fun onResult(result: Void?) {}
                    override fun onError(error: CallEndpointException) { Log.w(TAG, "route change failed", error) }
                })
            } else {
                @Suppress("DEPRECATION")
                setAudioRoute(if (on) CallAudioState.ROUTE_SPEAKER else CallAudioState.ROUTE_WIRED_OR_EARPIECE)
            }
        }

        override fun onDisconnect() = end(DisconnectCause.LOCAL)   // hang-up from the car or the system
        override fun onAbort() = end(DisconnectCause.LOCAL)
        override fun onReject() = end(DisconnectCause.REJECTED)

        fun end(cause: Int) {
            setDisconnected(DisconnectCause(cause))
            destroy()
            if (current === this) current = null
            VoiceCallService.stop(context)
        }
    }

    companion object {
        private const val TAG = "BaryonVoice"
        const val EXTRA_URL = "baryon.url"
        const val EXTRA_TOKEN = "baryon.token"
        @Volatile var current: SecretaryConnection? = null

        fun handle(context: Context) =
            PhoneAccountHandle(ComponentName(context, CallConnectionService::class.java), "baryon-secretary")

        fun register(context: Context) {
            val telecom = context.getSystemService(TelecomManager::class.java) ?: return
            val account = PhoneAccount.builder(handle(context), context.getString(R.string.secretary_name))
                .setCapabilities(PhoneAccount.CAPABILITY_SELF_MANAGED)
                .setSupportedUriSchemes(listOf(PhoneAccount.SCHEME_SIP))
                .build()
            telecom.registerPhoneAccount(account)
        }

        /** Place the call through Telecom; fall back to a plain voice session if that is not allowed. */
        fun place(context: Context, url: String, token: String): Boolean {
            val telecom = context.getSystemService(TelecomManager::class.java)
            return try {
                register(context)
                val extras = Bundle().apply {
                    putParcelable(TelecomManager.EXTRA_PHONE_ACCOUNT_HANDLE, handle(context))
                    putBundle(TelecomManager.EXTRA_OUTGOING_CALL_EXTRAS, Bundle().apply {
                        putString(EXTRA_URL, url)
                        putString(EXTRA_TOKEN, token)
                    })
                }
                telecom.placeCall(Uri.fromParts("sip", "secretary", null), extras)
                true
            } catch (error: SecurityException) {
                Log.w(TAG, "placeCall not allowed", error)
                VoiceCallService.start(context, url, token)
                false
            }
        }

        fun setSpeaker(context: Context, on: Boolean) {
            current?.setSpeaker(on) ?: AudioRoute.fallbackSpeaker(context, on)
        }

        fun hangUp(context: Context) {
            current?.end(DisconnectCause.LOCAL) ?: VoiceCallService.stop(context)
        }
    }
}
