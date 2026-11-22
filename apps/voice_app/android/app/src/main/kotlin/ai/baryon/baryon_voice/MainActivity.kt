package ai.baryon.baryon_voice

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ShortcutInfo
import android.content.pm.ShortcutManager
import android.graphics.drawable.Icon
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodChannel

/**
 * Bridges the Flutter screen to the native call engine.
 *
 * Settings (server URL and device token) live in SharedPreferences. For development they can
 * be provisioned over USB without typing:
 *   adb shell am start -n ai.baryon.baryon_voice/.MainActivity --es server <wss-url> --es token <token>
 */
class MainActivity : FlutterActivity() {
    private val main = Handler(Looper.getMainLooper())
    private var pendingStart: (() -> Unit)? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        provision(intent)
        publishShortcuts()
        callIfAsked(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        provision(intent)
        callIfAsked(intent)
    }

    /** "비서에게 전화" from the home screen or the icon's long-press menu: one tap, the call starts. */
    private fun callIfAsked(intent: Intent?) {
        if (intent?.action != ACTION_CALL_SECRETARY || VoiceEngine.isRunning) return
        intent.action = Intent.ACTION_MAIN // not again on rotation
        main.postDelayed({ withPermissions { startCall() } }, 300)
    }

    private fun startCall() {
        val token = prefs().getString("token", "").orEmpty()
        if (token.isBlank()) {
            VoiceEngine.emit("error", "message" to "설정에서 기기 토큰을 넣어 주세요.")
            return
        }
        if (VoiceEngine.isRunning && !VoiceEngine.audioCall) VoiceEngine.stop("switch")
        CallConnectionService.place(applicationContext, prefs().getString("server", DEFAULT_SERVER).orEmpty(), token)
    }

    private fun callShortcut(): ShortcutInfo =
        ShortcutInfo.Builder(this, "call-secretary")
            .setShortLabel(getString(R.string.call_secretary))
            .setLongLabel(getString(R.string.call_secretary))
            .setIcon(Icon.createWithResource(this, R.mipmap.ic_launcher))
            .setIntent(Intent(this, MainActivity::class.java).setAction(ACTION_CALL_SECRETARY))
            .build()

    private fun publishShortcuts() {
        try {
            getSystemService(ShortcutManager::class.java)?.dynamicShortcuts = listOf(callShortcut())
        } catch (_: Exception) {
        }
    }

    /** Ask the launcher to put a "비서에게 전화" icon on the home screen (the system shows a confirmation). */
    private fun pinShortcut(): Boolean {
        val manager = getSystemService(ShortcutManager::class.java) ?: return false
        if (!manager.isRequestPinShortcutSupported) return false
        return manager.requestPinShortcut(callShortcut(), null)
    }

    private fun provision(intent: Intent?) {
        val server = intent?.getStringExtra("server")
        val token = intent?.getStringExtra("token")
        if (server.isNullOrBlank() && token.isNullOrBlank()) return
        prefs().edit().apply {
            if (!server.isNullOrBlank()) putString("server", server)
            if (!token.isNullOrBlank()) putString("token", token)
        }.apply()
    }

    private fun prefs() = getSharedPreferences("baryon_voice", Context.MODE_PRIVATE)

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val messenger = flutterEngine.dartExecutor.binaryMessenger
        MethodChannel(messenger, "baryon/voice").setMethodCallHandler { call, result ->
            when (call.method) {
                "config" -> result.success(mapOf(
                    "server" to prefs().getString("server", DEFAULT_SERVER),
                    "hasToken" to !prefs().getString("token", "").isNullOrBlank(),
                    "inCall" to VoiceEngine.isRunning,
                ))
                "save" -> {
                    prefs().edit().apply {
                        call.argument<String>("server")?.takeIf { it.isNotBlank() }?.let { putString("server", it) }
                        call.argument<String>("token")?.takeIf { it.isNotBlank() }?.let { putString("token", it) }
                    }.apply()
                    result.success(true)
                }
                "start" -> withPermissions {
                    startCall()
                    result.success(true)
                }
                "pinShortcut" -> result.success(pinShortcut())
                "typing" -> { VoiceEngine.typing(); result.success(true) }
                "callPrefs" -> {
                    VoiceEngine.prefsJson = org.json.JSONObject().put("type", "prefs")
                        .put("earcons", call.argument<Boolean>("earcons") ?: true)
                        .put("voice", call.argument<Int>("voice") ?: 0)
                        .put("speed", call.argument<Double>("speed") ?: 1.05)
                        .put("filter", call.argument<String>("filter") ?: "strict").toString()
                    result.success(true)
                }
                "keepAwake" -> {
                    val on = call.argument<Boolean>("on") == true
                    if (on) window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                    else window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                    result.success(true)
                }
                "stop" -> { CallConnectionService.hangUp(applicationContext); result.success(true) }
                "text" -> {
                    // Typing works with or without a call: without one, open a text-only conversation.
                    if (!VoiceEngine.isRunning) startTyped()
                    VoiceEngine.sendText(call.argument<String>("text").orEmpty())
                    result.success(true)
                }
                "dictate" -> {
                    val on = call.argument<Boolean>("on") == true
                    if (on) withPermissions {
                        if (!VoiceEngine.isRunning) startTyped()
                        main.postDelayed({ VoiceEngine.dictate(true) }, if (VoiceEngine.isRunning) 0 else 600)
                    } else VoiceEngine.dictate(false)
                    result.success(true)
                }
                "mute" -> { VoiceEngine.setMuted(call.argument<Boolean>("muted") == true); result.success(true) }
                "speaker" -> { CallConnectionService.setSpeaker(applicationContext, call.argument<Boolean>("on") == true); result.success(true) }
                else -> result.notImplemented()
            }
        }
        EventChannel(messenger, "baryon/voice/events").setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink) {
                VoiceEngine.listener = { json -> main.post { events.success(json) } }
            }

            override fun onCancel(arguments: Any?) {
                VoiceEngine.listener = null
            }
        })
    }

    private fun startTyped() {
        val token = prefs().getString("token", "").orEmpty()
        if (token.isBlank()) {
            VoiceEngine.emit("error", "message" to "설정에서 기기 토큰을 넣어 주세요.")
            return
        }
        VoiceEngine.onEnded = null
        VoiceEngine.start(prefs().getString("server", DEFAULT_SERVER).orEmpty(), token, audio = false)
    }

    private fun withPermissions(then: () -> Unit) {
        val needed = buildList {
            add(Manifest.permission.RECORD_AUDIO)
            add(Manifest.permission.MANAGE_OWN_CALLS)
            if (Build.VERSION.SDK_INT >= 31) add(Manifest.permission.BLUETOOTH_CONNECT)
            if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
        }.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (needed.isEmpty()) then() else {
            pendingStart = then
            requestPermissions(needed.toTypedArray(), 42)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != 42) return
        val micGranted = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (micGranted) pendingStart?.invoke() else VoiceEngine.emit("error", "message" to "마이크 권한이 필요합니다.")
        pendingStart = null
    }

    companion object {
        // Development default: `adb reverse tcp:9093 tcp:9093` forwards this to the gateway over USB.
        const val DEFAULT_SERVER = "ws://127.0.0.1:9093/v1/voice/call"
        const val ACTION_CALL_SECRETARY = "ai.baryon.baryon_voice.CALL_SECRETARY"
    }
}
