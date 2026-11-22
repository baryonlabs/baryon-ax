package ai.baryon.baryon_voice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import kotlin.math.sqrt

/**
 * Recording a meeting, a lecture or a memo for the secretary to read (설정 없이 입력창의 + > 녹음).
 *
 * A foreground service, so it keeps recording with the screen off. Audio is 16 kHz mono PCM in the
 * app's cache (about 1.9 MB a minute, at most 40 minutes), taken from the plain microphone rather than
 * the call path, so voices across a table are kept. When it stops, the file is streamed to the gateway
 * (/v1/voice/upload), which turns it into time-stamped text; the app then sends that text to the
 * secretary. Events for the screen (VoiceEngine listener):
 *
 *   {"type": "recording", "seconds", "level"}      while recording, every half second
 *   {"type": "recorded", "path", "seconds"}         stopped
 *   {"type": "upload", "stage": sending|transcribing, "done", "total"}
 *   {"type": "transcript", "text", "seconds", "path"}
 *   {"type": "upload_error", "message"}
 */
class RecordService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            recording = false
            return START_NOT_STICKY
        }
        if (recording) return START_NOT_STICKY
        startForeground(NOTIFICATION_ID, notification(), if (Build.VERSION.SDK_INT >= 30) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0)
        val file = File(cacheDir, "recordings").apply { mkdirs() }.resolve("rec-${System.currentTimeMillis()}.pcm")
        recording = true
        thread(name = "recorder") { record(file) }
        return START_NOT_STICKY
    }

    private fun record(file: File) {
        val min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val recorder = try {
            AudioRecord(MediaRecorder.AudioSource.MIC, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min, RATE))
        } catch (_: SecurityException) {
            null
        }
        if (recorder == null || recorder.state != AudioRecord.STATE_INITIALIZED) {
            recording = false
            VoiceEngine.emit("upload_error", "message" to "마이크를 열 수 없어요.")
            finish()
            return
        }
        val buffer = ShortArray(RATE / 10)
        val bytes = ByteArray(buffer.size * 2)
        var samples = 0L
        var lastReport = 0L
        val started = SystemClock.elapsedRealtime()
        recorder.startRecording()
        FileOutputStream(file).use { out ->
            while (recording && samples < MAX_SECONDS * RATE) {
                val read = recorder.read(buffer, 0, buffer.size)
                if (read <= 0) continue
                var sum = 0.0
                for (i in 0 until read) {
                    val value = buffer[i].toInt()
                    bytes[2 * i] = (value and 0xff).toByte()
                    bytes[2 * i + 1] = (value shr 8 and 0xff).toByte()
                    sum += value.toDouble() * value
                }
                out.write(bytes, 0, read * 2)
                samples += read
                val now = SystemClock.elapsedRealtime()
                if (now - lastReport >= 500) {
                    lastReport = now
                    seconds = samples / RATE.toDouble()
                    VoiceEngine.emit("recording", "seconds" to seconds, "level" to sqrt(sum / read) / 32768.0)
                    if ((now - started) / 1000 % 5 == 0L) updateNotification()
                }
            }
        }
        recorder.stop()
        recorder.release()
        recording = false
        seconds = samples / RATE.toDouble()
        lastPath = file.absolutePath
        VoiceEngine.emit("recorded", "path" to file.absolutePath, "seconds" to seconds)
        finish()
    }

    private fun finish() {
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun updateNotification() {
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification())
    }

    private fun notification(): Notification {
        getSystemService(NotificationManager::class.java)
            .createNotificationChannel(NotificationChannel(CHANNEL, "녹음", NotificationManager.IMPORTANCE_LOW))
        val stop = PendingIntent.getService(this, 2, Intent(this, RecordService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE)
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val minutes = seconds.toInt() / 60
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("비서가 녹음 중")
            .setContentText(if (seconds > 0) "${minutes}분 ${seconds.toInt() % 60}초 · 멈추면 글로 옮겨 비서에게 보내요" else "멈추면 글로 옮겨 비서에게 보내요")
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .addAction(Notification.Action.Builder(null, "멈추기", stop).build())
            .build()
    }

    companion object {
        const val RATE = 16000
        const val MAX_SECONDS = 40 * 60
        private const val CHANNEL = "baryon-record"
        private const val NOTIFICATION_ID = 9
        private const val ACTION_STOP = "ai.baryon.baryon_voice.RECORD_STOP"

        @Volatile var recording = false
        @Volatile var seconds = 0.0
        /** The last finished recording, until the app takes it (stopped from the notification while away). */
        @Volatile var lastPath: String? = null

        fun start(context: Context) {
            context.startForegroundService(Intent(context, RecordService::class.java))
        }

        fun stop(context: Context) {
            context.startService(Intent(context, RecordService::class.java).setAction(ACTION_STOP))
        }

        private val client = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(10, TimeUnit.MINUTES) // a long recording takes a while to transcribe
            .build()

        fun uploadUrl(callUrl: String): String =
            callUrl.substringBefore("?").replace(Regex("/v1/voice/call$"), "") + "/v1/voice/upload"

        /** Stream a recording to the gateway and report its text (events above). */
        fun upload(callUrl: String, token: String, path: String) {
            val file = File(path)
            if (!file.exists()) {
                VoiceEngine.emit("upload_error", "message" to "녹음 파일이 없어요.")
                return
            }
            val request = Request.Builder().url(uploadUrl(callUrl)).header("Authorization", "Bearer $token").build()
            client.newWebSocket(request, object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    thread(name = "upload") {
                        webSocket.send(JSONObject().put("type", "recording").put("rate", RATE).toString())
                        val total = file.length()
                        var sent = 0L
                        file.inputStream().use { input ->
                            val chunk = ByteArray(64 * 1024)
                            while (true) {
                                val read = input.read(chunk)
                                if (read <= 0) break
                                while (webSocket.queueSize() > 4L * 1024 * 1024) Thread.sleep(50) // do not buffer it all
                                webSocket.send(chunk.toByteString(0, read))
                                sent += read
                                VoiceEngine.emit("upload", "stage" to "sending", "done" to sent, "total" to total)
                            }
                        }
                        webSocket.send(JSONObject().put("type", "end").toString())
                    }
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    val event = try { JSONObject(text) } catch (_: Exception) { return }
                    when (event.optString("type")) {
                        "progress" -> VoiceEngine.emit("upload", "stage" to "transcribing",
                            "done" to event.optInt("done"), "total" to event.optInt("total"))
                        "transcript" -> {
                            VoiceEngine.emit("transcript", "text" to event.optString("text"),
                                "seconds" to event.optDouble("seconds"), "path" to path)
                            webSocket.close(1000, null)
                        }
                        "error" -> {
                            VoiceEngine.emit("upload_error", "message" to event.optString("message"))
                            webSocket.close(1000, null)
                        }
                    }
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    VoiceEngine.emit("upload_error", "message" to if (response?.code == 401) "기기 토큰이 맞지 않아요" else "서버에 보내지 못했어요. 녹음은 남아 있어요.")
                }
            })
        }
    }
}
