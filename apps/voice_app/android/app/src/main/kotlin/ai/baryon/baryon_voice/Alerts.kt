package ai.baryon.baryon_voice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/**
 * How a secretary notification reaches the owner.
 *
 *   normal  a heads-up notification with the notification sound and a vibration; the screen lights up
 *           for a few seconds so it can be read without touching the phone
 *   alarm   rings with the alarm sound (it plays even on silent, like an alarm clock) and vibrates until
 *           dismissed or for two minutes, and opens AlarmActivity over the lock screen with the screen on
 */
object Alerts {
    private const val ALERT_CHANNEL = "baryon-alert"
    private const val ALARM_CHANNEL = "baryon-alarm"
    private const val RING_MILLIS = 120_000L
    const val ACTION_SILENCE = "ai.baryon.baryon_voice.SILENCE"
    const val EXTRA_ID = "id"
    const val EXTRA_TITLE = "title"
    const val EXTRA_TEXT = "text"

    private val main = Handler(Looper.getMainLooper())
    private var player: MediaPlayer? = null
    private var vibrator: Vibrator? = null
    private var ringingId: String? = null

    fun channels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(ALERT_CHANNEL, "비서 알림", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "비서가 알려 주는 일: 예약한 알림, 끝난 작업"
            enableVibration(true)
            setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION),
                AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION).build())
        })
        manager.createNotificationChannel(NotificationChannel(ALARM_CHANNEL, "비서 알람", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "깨우기처럼 놓치면 안 되는 알림: 화면을 켜고 끌 때까지 울려요"
            setSound(null, null) // the alarm sound is played by Alerts itself, looping, on the alarm stream
            enableVibration(false)
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
        })
    }

    fun show(context: Context, id: String, title: String, text: String, alarm: Boolean) {
        channels(context)
        val number = id.hashCode()
        val open = PendingIntent.getActivity(context, number, Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE)
        val builder = Notification.Builder(context, if (alarm) ALARM_CHANNEL else ALERT_CHANNEL)
            .setSmallIcon(android.R.drawable.ic_popup_reminder)
            .setContentTitle(title.ifBlank { "비서 알림" })
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .setContentIntent(open)
            .setCategory(if (alarm) Notification.CATEGORY_ALARM else Notification.CATEGORY_REMINDER)
            .setVisibility(Notification.VISIBILITY_PUBLIC)
        if (alarm) {
            val screen = PendingIntent.getActivity(context, number,
                Intent(context, AlarmActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_USER_ACTION)
                    .putExtra(EXTRA_ID, id).putExtra(EXTRA_TITLE, title).putExtra(EXTRA_TEXT, text),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val silence = PendingIntent.getBroadcast(context, number,
                Intent(context, SilenceReceiver::class.java).setAction(ACTION_SILENCE).putExtra(EXTRA_ID, id),
                PendingIntent.FLAG_IMMUTABLE)
            builder.setFullScreenIntent(screen, true)
                .setContentIntent(screen)
                .setOngoing(true)
                .addAction(Notification.Action.Builder(null, "끄기", silence).build())
            ring(context, id)
        }
        context.getSystemService(NotificationManager::class.java).notify(number, builder.build())
        wakeScreen(context, if (alarm) 30_000L else 8_000L)
    }

    /** Light the screen even when the phone is asleep (the notification is readable at a glance). */
    @Suppress("DEPRECATION") // ACQUIRE_CAUSES_WAKEUP with a screen lock is still the only way from a service
    private fun wakeScreen(context: Context, millis: Long) {
        val power = context.getSystemService(PowerManager::class.java)
        if (power.isInteractive) return
        power.newWakeLock(PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP or PowerManager.ON_AFTER_RELEASE,
            "baryon:alert").acquire(millis)
    }

    private fun ring(context: Context, id: String) {
        silence(context, null)
        ringingId = id
        val uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
        player = try {
            MediaPlayer().apply {
                setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
                setDataSource(context, uri)
                isLooping = true
                prepare()
                start()
            }
        } catch (_: Exception) {
            null
        }
        vibrator = (if (Build.VERSION.SDK_INT >= 31) context.getSystemService(VibratorManager::class.java).defaultVibrator
                    else @Suppress("DEPRECATION") context.getSystemService(Vibrator::class.java))
            .also { it.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 800, 600), 0)) }
        main.postDelayed({ if (ringingId == id) silence(context, id) }, RING_MILLIS)
    }

    /** Stop the ringing (id null: whatever rings) and clear that alarm's notification. */
    fun silence(context: Context, id: String?) {
        if (id != null && ringingId != null && id != ringingId) return
        player?.run { try { stop() } catch (_: Exception) {}; release() }
        player = null
        vibrator?.cancel()
        vibrator = null
        ringingId?.let { context.getSystemService(NotificationManager::class.java).cancel(it.hashCode()) }
        ringingId = null
    }

    val ringing: Boolean get() = ringingId != null
}

/** "끄기" on the alarm notification. */
class SilenceReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        Alerts.silence(context, intent.getStringExtra(Alerts.EXTRA_ID))
    }
}

/** Notifications come back after a reboot or an app update without opening the app. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action in setOf(Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED, "android.intent.action.QUICKBOOT_POWERON")) {
            NotifyService.startIfWanted(context)
        }
    }
}
