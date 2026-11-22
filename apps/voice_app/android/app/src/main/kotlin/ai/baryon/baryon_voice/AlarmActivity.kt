package ai.baryon.baryon_voice

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * The alarm screen: shown over the lock screen with the screen turned on, the way an alarm clock does.
 * Plain Android views (no Flutter engine to start), so it appears at once. 끄기 stops the sound;
 * 비서에게 전화 stops it and calls the secretary, for "깨웠으면 오늘 일정 알려 줘".
 */
class AlarmActivity : Activity() {
    private var id: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        show(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        show(intent)
    }

    private fun show(intent: Intent) {
        id = intent.getStringExtra(Alerts.EXTRA_ID)
        val density = resources.displayMetrics.density
        fun px(dp: Int) = (dp * density).toInt()
        fun label(text: String, size: Float, bold: Boolean = false, alpha: Float = 1f) = TextView(this).apply {
            this.text = text
            textSize = size
            setTextColor(Color.WHITE)
            this.alpha = alpha
            gravity = Gravity.CENTER
            if (bold) setTypeface(typeface, Typeface.BOLD)
        }
        fun button(text: String, color: Int, onClick: () -> Unit) = Button(this).apply {
            this.text = text
            textSize = 20f
            setTextColor(Color.WHITE)
            setBackgroundColor(color)
            setOnClickListener { onClick() }
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, px(64)).apply { topMargin = px(14) }
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(Color.rgb(16, 24, 40))
            setPadding(px(28), px(48), px(28), px(48))
            addView(label(SimpleDateFormat("a h:mm", Locale.KOREA).format(Date()), 22f, alpha = 0.7f))
            addView(label(intent.getStringExtra(Alerts.EXTRA_TITLE).orEmpty().ifBlank { "비서 알람" }, 34f, bold = true).apply {
                setPadding(0, px(18), 0, px(12))
            })
            addView(label(intent.getStringExtra(Alerts.EXTRA_TEXT).orEmpty(), 20f, alpha = 0.85f), LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
            addView(LinearLayout(this@AlarmActivity).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(0, px(48), 0, 0)
                addView(button("끄기", Color.rgb(220, 60, 60)) { dismiss() })
                addView(button("비서에게 전화", Color.rgb(40, 110, 220)) {
                    dismiss()
                    startActivity(Intent(this@AlarmActivity, MainActivity::class.java)
                        .setAction(MainActivity.ACTION_CALL_SECRETARY).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                })
            }, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        })
    }

    private fun dismiss() {
        Alerts.silence(this, id)
        finish()
    }
}
