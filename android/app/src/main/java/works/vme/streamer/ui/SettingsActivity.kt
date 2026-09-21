package works.vme.streamer.ui

import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.text.InputType
import android.text.method.PasswordTransformationMethod
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import works.vme.streamer.data.Settings
import works.vme.streamer.data.VmeClient

/**
 * Backend URL and credentials.
 *
 * Separate screen rather than a section on Home because these are
 * set once per device and then never touched -- putting them on the
 * screen the operator sees before every match would be three fields
 * of permanent noise above the thing they actually came to do.
 *
 * The **Test** button is the point of the page. Saving a URL tells
 * you nothing; the failure modes here (wrong port, backend not
 * started, Basic auth not enabled on IIS, typo'd password) are all
 * invisible until something tries to use them, and the natural
 * moment to discover that is *not* in the gym ten minutes before
 * first serve. Test does a real authenticated round trip and reports
 * what came back.
 */
class SettingsActivity : ComponentActivity() {

    private lateinit var urlInput: EditText
    private lateinit var userInput: EditText
    private lateinit var passInput: EditText
    private lateinit var statusView: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = buildUi()
        setContentView(root)
        root.applySystemBarInsets()

        urlInput.setText(Settings.baseUrl(this))
        userInput.setText(Settings.user(this))
        passInput.setText(Settings.password(this))
    }

    private fun buildUi(): View {
        val scroll = ScrollView(this).apply { setBackgroundColor(Color.BLACK) }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 24, 24, 24)
        }

        root.addView(TextView(this).apply {
            text = "Settings"
            setTextColor(Color.WHITE)
            textSize = 22f
            typeface = Typeface.DEFAULT_BOLD
        })

        root.addView(label("VME server"))
        urlInput = field("http://192.168.1.20:8000").apply {
            inputType = InputType.TYPE_TEXT_VARIATION_URI
        }
        root.addView(urlInput)
        root.addView(hint("Scheme, host and port. No trailing slash needed."))

        root.addView(label("Credentials"))
        // The VME backend sits behind Windows Auth in production.
        // Android's stock HttpURLConnection cannot speak
        // Negotiate/NTLM, so the site has to have Basic enabled
        // alongside it server-side. Worth stating here because the
        // symptom otherwise is a bare 401 with no clue what to fix.
        userInput = field("user (blank if the backend has no auth)")
        root.addView(userInput)

        passInput = field("password").apply {
            // Both are needed: inputType alone gets ignored by some
            // IMEs, and the transformation method alone fails to
            // hide the last typed character.
            inputType = InputType.TYPE_CLASS_TEXT or
                        InputType.TYPE_TEXT_VARIATION_PASSWORD
            transformationMethod = PasswordTransformationMethod.getInstance()
            typeface = Typeface.MONOSPACE
        }
        root.addView(passInput)
        root.addView(hint("DOMAIN\\user, user@domain and plain user all work. " +
            "Leave both blank for a backend with no auth."))

        val actions = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 16, 0, 8)
        }
        actions.addView(Button(this).apply {
            text = "Save"
            setOnClickListener { save(); finish() }
        }, rowParams())
        actions.addView(Button(this).apply {
            text = "Test"
            setOnClickListener { save(); test() }
        }, rowParams())
        root.addView(actions)

        statusView = TextView(this).apply {
            setTextColor(Color.parseColor("#CCCCCC"))
            textSize = 13f
            typeface = Typeface.MONOSPACE
            setPadding(0, 8, 0, 0)
        }
        root.addView(statusView)

        scroll.addView(root)
        return scroll
    }

    // ---- actions ----------------------------------------------------

    private fun save() {
        Settings.save(
            this,
            urlInput.text.toString(),
            userInput.text.toString(),
            passInput.text.toString(),
        )
    }

    /**
     * Real round trip against `/api/teams`. Chosen as the probe
     * because it is the same call Import makes first, needs auth,
     * and returns something countable -- so a success message can
     * say "8 teams" rather than a bare "OK" the operator has to
     * take on faith.
     */
    private fun test() {
        val base = Settings.baseUrl(this)
        if (base.isBlank()) {
            status("Enter a URL first.", ERROR)
            return
        }
        val creds = Settings.credentials(this)
        status("GET $base/api/teams ...", NEUTRAL)
        Thread {
            try {
                val teams = VmeClient.listTeams(base, creds)
                runOnUiThread {
                    status(
                        "OK - ${teams.size} team${if (teams.size == 1) "" else "s"}" +
                            if (creds != null) " (auth: ${creds.user})" else " (no auth)",
                        OK,
                    )
                }
            } catch (exc: Exception) {
                runOnUiThread { status(explain(exc), ERROR) }
            }
        }.start()
    }

    /**
     * Turn the exception into something actionable. A raw
     * `FileNotFoundException` or bare 401 tells the operator
     * nothing about which of the four fields is wrong.
     */
    private fun explain(exc: Exception): String {
        val msg = exc.message.orEmpty()
        return when {
            msg.contains("401") ->
                "401 Unauthorized - wrong user/password, or the IIS site " +
                    "does not have Basic auth enabled alongside Windows Auth."
            msg.contains("403") ->
                "403 Forbidden - reached the server, but this account " +
                    "cannot read the API."
            msg.contains("404") ->
                "404 - reached a server, but no VME API at this URL. " +
                    "Check the port and any path prefix."
            exc is java.net.SocketTimeoutException ->
                "Timed out - server unreachable from this network. " +
                    "Same Wi-Fi as the backend?"
            exc is java.net.ConnectException ->
                "Connection refused - nothing listening on that port."
            exc is java.net.UnknownHostException ->
                "Unknown host - check the hostname, or use the IP."
            else -> "${exc.javaClass.simpleName}: $msg"
        }
    }

    // ---- view helpers -----------------------------------------------

    private fun status(text: String, color: Int) {
        statusView.setTextColor(color)
        statusView.text = text
    }

    private fun label(text: String) = TextView(this).apply {
        this.text = text
        setTextColor(Color.parseColor("#9E9E9E"))
        textSize = 12f
        typeface = Typeface.DEFAULT_BOLD
        setPadding(0, 20, 0, 4)
    }

    private fun hint(text: String) = TextView(this).apply {
        this.text = text
        setTextColor(Color.parseColor("#6E6E6E"))
        textSize = 11f
        setPadding(0, 4, 0, 0)
    }

    private fun field(hintText: String) = EditText(this).apply {
        hint = hintText
        setTextColor(Color.WHITE)
        setHintTextColor(Color.GRAY)
        setSingleLine()
    }

    private fun rowParams() = LinearLayout.LayoutParams(
        0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f,
    )

    private companion object {
        val OK: Int = Color.parseColor("#4CAF50")
        val ERROR: Int = Color.parseColor("#FF5252")
        val NEUTRAL: Int = Color.parseColor("#CCCCCC")
    }
}
