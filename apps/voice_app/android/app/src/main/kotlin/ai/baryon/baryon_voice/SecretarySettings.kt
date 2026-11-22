package ai.baryon.baryon_voice

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * The "비서에게 알려 주기" screen talks to the secretary through the gateway's settings channel
 * (/v1/voice/settings, same device token as calls). One short WebSocket per request: open,
 * send, take the answer, close. The secretary's own key stays on the server.
 */
object SecretarySettings {
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build()

    /** The call URL (…/v1/voice/call) with the settings path instead. */
    fun settingsUrl(callUrl: String): String =
        callUrl.substringBefore("?").replace(Regex("/v1/voice/call$"), "") + "/v1/voice/settings"

    fun request(callUrl: String, token: String, body: JSONObject, done: (String) -> Unit) {
        var answered = false
        fun answer(text: String) {
            if (!answered) { answered = true; done(text) }
        }
        val request = Request.Builder().url(settingsUrl(callUrl)).header("Authorization", "Bearer $token").build()
        client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(body.put("id", 1).toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                answer(text)
                webSocket.close(1000, null)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                val reason = if (response?.code == 401) "기기 토큰이 맞지 않아요" else "서버에 연결할 수 없어요"
                answer(JSONObject().put("ok", false).put("error", reason).toString())
            }
        })
    }
}
