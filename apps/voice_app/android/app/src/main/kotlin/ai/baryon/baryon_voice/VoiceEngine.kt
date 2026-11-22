package ai.baryon.baryon_voice

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * The call itself: microphone -> gateway, gateway -> speaker, over one WebSocket.
 *
 * Audio is 16 kHz mono PCM16 in 20 ms frames. The microphone uses the VOICE_COMMUNICATION
 * source with the platform echo canceller, so the secretary's own voice coming out of the
 * speaker is not heard back as the caller (the echo problem seen with plain SIP calls).
 */
object VoiceEngine {
    private const val TAG = "BaryonVoice"
    private const val RATE = 16000
    private const val FRAME_BYTES = RATE / 50 * 2 // 20 ms

    /** JSON events for the Flutter screen. */
    @Volatile var listener: ((String) -> Unit)? = null
    /** Called once when the line goes down, whoever ended it. */
    @Volatile var onEnded: (() -> Unit)? = null

    @Volatile private var running = false
    /** false for a typed conversation: no microphone stream, no speech played. */
    @Volatile var audioCall = true
        private set
    @Volatile private var open = false
    private val pendingTexts = mutableListOf<String>()
    @Volatile private var dictating = false
    private var dictationThread: Thread? = null
    /** Short status for the ongoing-call notification. */
    @Volatile var onStatus: ((String) -> Unit)? = null
    private var socket: WebSocket? = null
    private var recorder: AudioRecord? = null
    private var track: AudioTrack? = null
    private val playback = LinkedBlockingQueue<ByteArray>()
    private var recordThread: Thread? = null
    private var playThread: Thread? = null
    private val effects = mutableListOf<android.media.audiofx.AudioEffect>()

    private val client = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    val isRunning get() = running

    fun emit(type: String, vararg pairs: Pair<String, Any?>) {
        val json = JSONObject().put("type", type)
        pairs.forEach { (key, value) -> json.put(key, value) }
        listener?.invoke(json.toString())
    }

    @Synchronized
    fun start(url: String, token: String, audio: Boolean = true) {
        if (running) return
        running = true
        audioCall = audio
        open = false
        emit("state", "state" to "connecting", "audio" to audio)
        val target = if (audio) url else url + (if ('?' in url) "&" else "?") + "audio=0"
        val request = Request.Builder().url(target).header("Authorization", "Bearer $token").build()
        socket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                open = true
                emit("state", "state" to if (audio) "connected" else "typing", "audio" to audio)
                if (audio) startAudio(webSocket)
                prefsJson?.let { webSocket.send(it) }
                synchronized(pendingTexts) { pendingTexts.forEach { sendText(it) }; pendingTexts.clear() }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val event = JSONObject(text)
                    when (event.optString("type")) {
                        "flush" -> flushPlayback()
                        "activity" -> onStatus?.invoke(event.optString("label"))
                        "heard" -> onStatus?.invoke("생각하는 중…")
                        "idle" -> onStatus?.invoke("듣고 있어요")
                    }
                } catch (_: Exception) {
                }
                listener?.invoke(text)
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                playback.offer(bytes.toByteArray())
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
                stop("closed")
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.w(TAG, "connection failed", t)
                val reason = when (response?.code) {
                    401 -> "인증 토큰이 맞지 않습니다."
                    404 -> "서버 주소가 맞지 않습니다."
                    else -> "서버에 연결할 수 없습니다."
                }
                emit("error", "message" to reason)
                stop("failed")
            }
        })
    }

    /** The caller's settings for this call (earcons, voice, speed); resent whenever they change. */
    @Volatile var prefsJson: String? = null
        set(value) {
            field = value
            if (open && value != null) socket?.send(value)
        }

    /** The caller is typing: the gateway ignores key clicks on the microphone for a moment. */
    fun typing() {
        if (open) socket?.send("""{"type":"typing"}""")
    }

    /** Typed message or a tapped answer such as "응". Queued until the line is open. */
    fun sendText(text: String) {
        if (!open) {
            synchronized(pendingTexts) { pendingTexts += text }
            return
        }
        socket?.send(JSONObject().put("type", "text").put("text", text).toString())
    }

    /**
     * Speech to text for the input box. In a voice call the microphone already streams; in a
     * typed conversation a short recording is streamed just for this. The server returns a
     * "dictation" event with the text and does not send it to the secretary.
     */
    @Suppress("MissingPermission")
    fun dictate(on: Boolean) {
        val ws = socket ?: return
        if (on == dictating) return
        dictating = on
        ws.send(JSONObject().put("type", "dictate").put("on", on).toString())
        if (audioCall) return
        if (on) {
            val min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            val rec = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, maxOf(min, FRAME_BYTES * 4))
            if (rec.state != AudioRecord.STATE_INITIALIZED) { emit("error", "message" to "마이크를 열 수 없습니다."); return }
            rec.startRecording()
            dictationThread = Thread({
                val buffer = ByteArray(FRAME_BYTES)
                while (dictating) {
                    val n = rec.read(buffer, 0, FRAME_BYTES)
                    if (n > 0) ws.send(buffer.toByteString(0, n))
                }
                try { rec.stop() } catch (_: Exception) {}
                rec.release()
            }, "baryon-dictation").apply { start() }
        }
    }

    fun setMuted(muted: Boolean) {
        this.muted = muted
        emit("state", "state" to if (muted) "muted" else "connected")
    }

    @Volatile private var muted = false

    @Synchronized
    fun stop(reason: String = "hangup") {
        if (!running) return
        running = false
        open = false
        dictating = false
        socket?.let {
            try { it.send("""{"type":"hangup"}"""); it.close(1000, null) } catch (_: Exception) {}
        }
        socket = null
        recordThread?.interrupt()
        playThread?.interrupt()
        try { recorder?.stop() } catch (_: Exception) {}
        recorder?.release(); recorder = null
        effects.forEach { try { it.release() } catch (_: Exception) {} }
        effects.clear()
        try { track?.stop() } catch (_: Exception) {}
        track?.release(); track = null
        playback.clear()
        emit("state", "state" to "ended", "reason" to reason)
        onEnded?.invoke()
    }

    private fun flushPlayback() {
        playback.clear()
        track?.let { try { it.pause(); it.flush(); it.play() } catch (_: Exception) {} }
    }

    @Suppress("MissingPermission") // RECORD_AUDIO is requested before a call starts
    private fun startAudio(webSocket: WebSocket) {
        val inMin = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val rec = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION, RATE,
            AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(inMin, FRAME_BYTES * 4),
        )
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            emit("error", "message" to "마이크를 열 수 없습니다.")
            stop("mic")
            return
        }
        recorder = rec
        if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(rec.audioSessionId)?.let { it.enabled = true; effects += it }
        if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(rec.audioSessionId)?.let { it.enabled = true; effects += it }
        if (AutomaticGainControl.isAvailable()) AutomaticGainControl.create(rec.audioSessionId)?.let { it.enabled = true; effects += it }

        val outMin = AudioTrack.getMinBufferSize(RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val out = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder().setSampleRate(RATE).setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build(),
            )
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(maxOf(outMin, FRAME_BYTES * 8))
            .setSessionId(rec.audioSessionId) // same session: the echo canceller knows what is playing
            .build()
        track = out
        out.play()
        rec.startRecording()
        emit("mic", "echo_canceller" to effects.any { it is AcousticEchoCanceler })

        recordThread = Thread({
            val buffer = ByteArray(FRAME_BYTES)
            val silence = ByteArray(FRAME_BYTES)
            while (running && !Thread.currentThread().isInterrupted) {
                var read = 0
                while (read < FRAME_BYTES && running) {
                    val n = rec.read(buffer, read, FRAME_BYTES - read)
                    if (n <= 0) break
                    read += n
                }
                if (read == FRAME_BYTES) webSocket.send((if (muted) silence else buffer).toByteString(0, FRAME_BYTES))
            }
        }, "baryon-mic").apply { start() }

        playThread = Thread({
            while (running && !Thread.currentThread().isInterrupted) {
                val frame = try { playback.take() } catch (_: InterruptedException) { break }
                out.write(frame, 0, frame.size)
            }
        }, "baryon-speaker").apply { start() }
    }
}
