package ai.baryon.baryon_voice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * The secretary's own notifications, without a push service (no FCM, nothing leaves the tailnet).
 *
 * A foreground service keeps one WebSocket to the gateway (/v1/voice/notify, the same device token as
 * calls) and reconnects whenever it drops. Each notification is shown with sound (Alerts.show); an alarm
 * also turns the screen on and rings until dismissed. The service starts with the app and after a reboot
 * (BootReceiver), and stops only when the owner turns 알림 off in the settings.
 */
class NotifyService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private var socket: WebSocket? = null
    private var stopped = false
    private var retry = 2_000L
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS)
        .pingInterval(25, TimeUnit.SECONDS) // notices a dead connection (Wi-Fi to LTE) within a minute
        .build()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopped = true
            socket?.close(1000, null)
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(NOTIFICATION_ID, waiting("연결하는 중"), foregroundType())
        stopped = false
        if (socket == null) connect()
        return START_STICKY // the system restarts it if it has to kill it
    }

    override fun onDestroy() {
        stopped = true
        socket?.close(1000, null)
        socket = null
        connected = false
        main.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    private fun prefs() = getSharedPreferences("baryon_voice", Context.MODE_PRIVATE)

    private fun connect() {
        val token = prefs().getString("token", "").orEmpty()
        val callUrl = prefs().getString("server", MainActivity.DEFAULT_SERVER).orEmpty()
        if (token.isBlank()) {
            status("기기 토큰이 없어요")
            return
        }
        val request = Request.Builder().url(notifyUrl(callUrl)).header("Authorization", "Bearer $token").build()
        socket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                retry = 2_000L
                connected = true
                status("알림 기다리는 중")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val event = try { JSONObject(text) } catch (_: Exception) { return }
                if (event.optString("type") != "notify") return
                val id = event.optString("id")
                if (id.isNotBlank() && firstTime(id)) {
                    main.post { Alerts.show(this@NotifyService, id, event.optString("title"), event.optString("text"), event.optBoolean("alarm")) }
                }
                webSocket.send(JSONObject().put("type", "ack").put("id", id).toString())
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = dropped(webSocket, "끊김")

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.w(TAG, "notify socket: ${t.message}")
                dropped(webSocket, if (response?.code == 401) "기기 토큰이 맞지 않아요" else "서버에 다시 연결하는 중")
            }
        })
    }

    private fun dropped(webSocket: WebSocket, why: String) {
        if (socket !== webSocket) return
        socket = null
        connected = false
        if (stopped) return
        status(why)
        main.postDelayed({ if (!stopped && socket == null) connect() }, retry)
        retry = (retry * 2).coerceAtMost(60_000L)
    }

    /** Ids already shown: the gateway sends an unacknowledged notification again after a reconnect. */
    private fun firstTime(id: String): Boolean {
        val seen = prefs().getString("alerts_seen", "").orEmpty().split(",").filter { it.isNotBlank() }
        if (id in seen) return false
        prefs().edit().putString("alerts_seen", (seen + id).takeLast(100).joinToString(",")).apply()
        return true
    }

    private fun status(text: String) {
        lastStatus = text
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, waiting(text))
    }

    private fun foregroundType(): Int =
        if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0

    /** The quiet, always-there notification a foreground service must show. */
    private fun waiting(text: String): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "알림 연결", NotificationManager.IMPORTANCE_MIN).apply {
            description = "비서 알림을 받으려고 서버와 연결을 유지해요"
            setShowBadge(false)
        })
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_popup_reminder)
            .setContentTitle("비서 알림")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    companion object {
        private const val TAG = "NotifyService"
        private const val CHANNEL = "baryon-notify-link"
        private const val NOTIFICATION_ID = 8
        private const val ACTION_STOP = "ai.baryon.baryon_voice.NOTIFY_STOP"

        @Volatile var connected = false
        @Volatile var lastStatus = "꺼짐"

        /** The call URL (…/v1/voice/call) with the notify path instead. */
        fun notifyUrl(callUrl: String): String =
            callUrl.substringBefore("?").replace(Regex("/v1/voice/call$"), "") + "/v1/voice/notify"

        fun enabled(context: Context): Boolean =
            context.getSharedPreferences("baryon_voice", Context.MODE_PRIVATE).getBoolean("alerts", true)

        /** Start if the owner wants notifications and the phone has a device token. */
        fun startIfWanted(context: Context) {
            val prefs = context.getSharedPreferences("baryon_voice", Context.MODE_PRIVATE)
            if (!enabled(context) || prefs.getString("token", "").isNullOrBlank()) return
            try {
                context.startForegroundService(Intent(context, NotifyService::class.java))
            } catch (error: Exception) {
                Log.w(TAG, "cannot start: ${error.message}")
            }
        }

        fun stop(context: Context) {
            context.startService(Intent(context, NotifyService::class.java).setAction(ACTION_STOP))
            lastStatus = "꺼짐"
        }
    }
}
