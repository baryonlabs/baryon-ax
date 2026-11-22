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
import android.os.IBinder

/** Keeps the call alive with the screen off or another app in front (foreground service). */
class VoiceCallService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            VoiceEngine.stop()
            stopSelf()
            return START_NOT_STICKY
        }
        val url = intent?.getStringExtra(EXTRA_URL).orEmpty()
        val token = intent?.getStringExtra(EXTRA_TOKEN).orEmpty()
        startForeground(NOTIFICATION_ID, notification(), foregroundType())
        VoiceEngine.onEnded = {
            CallConnectionService.current?.end(android.telecom.DisconnectCause.REMOTE)
            stopSelf()
        }
        VoiceEngine.onStatus = { status -> updateStatus(status) }
        VoiceEngine.start(url, token)
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        VoiceEngine.stop()
        super.onDestroy()
    }

    private fun foregroundType(): Int =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R)
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or ServiceInfo.FOREGROUND_SERVICE_TYPE_PHONE_CALL
        else 0

    /** Keep the ongoing-call notification current, so a glance shows what the secretary is doing. */
    private fun updateStatus(status: String) {
        if (status.isBlank()) return
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification(status))
    }

    private fun notification(status: String? = null): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, getString(R.string.secretary_name), NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val hangUp = PendingIntent.getService(this, 1, Intent(this, VoiceCallService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.sym_action_call)
            .setContentTitle(getString(R.string.secretary_name))
            .setContentText(status ?: getString(R.string.in_call))
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(Notification.CATEGORY_CALL)
            .addAction(Notification.Action.Builder(null, getString(R.string.hang_up), hangUp).build())
            .build()
    }

    companion object {
        private const val CHANNEL = "baryon-call"
        private const val NOTIFICATION_ID = 7
        private const val ACTION_STOP = "ai.baryon.baryon_voice.STOP"
        private const val EXTRA_URL = "url"
        private const val EXTRA_TOKEN = "token"

        fun start(context: Context, url: String, token: String) {
            context.startForegroundService(Intent(context, VoiceCallService::class.java).putExtra(EXTRA_URL, url).putExtra(EXTRA_TOKEN, token))
        }

        fun stop(context: Context) {
            context.startService(Intent(context, VoiceCallService::class.java).setAction(ACTION_STOP))
        }
    }
}
