package works.vme.streamer.ui

import android.Manifest
import android.app.AlertDialog
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.graphics.Color
import android.graphics.Typeface
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Bundle
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.method.ScrollingMovementMethod
import android.text.style.ForegroundColorSpan
import android.text.style.RelativeSizeSpan
import android.text.style.StyleSpan
import android.view.Gravity
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.pedro.common.ConnectChecker
import com.pedro.common.VideoCodec
import com.pedro.encoder.input.sources.audio.MicrophoneSource
import com.pedro.encoder.input.sources.video.BitmapSource
import com.pedro.library.generic.GenericStream
import works.vme.streamer.BuildConfig
import works.vme.streamer.StreamService
import works.vme.streamer.UvcVideoSource
import works.vme.streamer.Mail
import works.vme.streamer.YouTubeAuth
import works.vme.streamer.YouTubeLive
import works.vme.streamer.data.EventType
import works.vme.streamer.data.GameState
import works.vme.streamer.data.Match
import works.vme.streamer.data.MatchEvent
import works.vme.streamer.data.jerseyLabel
import works.vme.streamer.data.MatchStore
import works.vme.streamer.data.PhotoCache
import works.vme.streamer.data.Player
import works.vme.streamer.data.Roster
import works.vme.streamer.data.Settings
import works.vme.streamer.data.Side
import works.vme.streamer.data.StreamConfig
import works.vme.streamer.data.StreamQuality
import works.vme.streamer.data.TeamStore
import works.vme.streamer.data.VmeClient
import works.vme.streamer.logic.GameStateEngine
import works.vme.streamer.overlay.BlackoutOverlay
import works.vme.streamer.overlay.CardOverlay
import works.vme.streamer.overlay.CourtOverlay
import works.vme.streamer.overlay.PopupOverlay
import works.vme.streamer.overlay.ScoreboardOverlay
import works.vme.streamer.overlay.StreamThumbnail
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * The match-day screen. Portrait-first layout modelled on the VME
 * courtside editor:
 *
 *   header:  [Events] [Preview]  <status>  [Start] [Stop] [Export]
 *   events:  big score readout
 *            home team card (name + serve dot + Sub + -1 + +1,
 *                            6-slot rotation grid, player action buttons)
 *            away team card (name + serve dot + -1 + +1)
 *            MATCH section (ball served, replay, cuts, game start/end,
 *                           set end, first serve H/A)
 *   preview: SurfaceView + streaming log
 *
 * Every tap goes through `record()` -> `store.save()` so the on-disk
 * match.json is authoritative. Two flows ask the operator to confirm:
 *
 *   1. `+1` when the last score has not yet been followed by a
 *      `ball_served` event (i.e. `gameState.ballServedSinceLastScore
 *      == false`). Catches mis-taps where the operator adds a point
 *      before the rally actually started.
 *   2. `-1` on either team. There is no undo in this UI; a subtraction
 *      is almost always a correction of a previous mis-tap, so making
 *      it confirm-once removes the "did I just double-tap?" risk.
 *
 * A `-1` is recorded as a `score_correction` event with a delta of -1
 * on the chosen side. VME's engine already knows how to fold those.
 */
class MatchLiveActivity : ComponentActivity(), ConnectChecker {

    // ---- persistent state -------------------------------------------

    private lateinit var store: MatchStore
    private lateinit var match: Match

    /** Jerseys designated libero for this match (see [pickLiberos]). */
    private val liberos: Set<Int> get() = match.liberos
    private val gameState get() = GameStateEngine.compute(match.events, liberos)

    // ---- streaming plumbing --------------------------------------

    private val target = UvcVideoSource.Target(width = 1920, height = 1080)
    // Rebuilt when the adapter is replugged: a source whose camera
    // helper has been released cannot be restarted, so recovery means
    // a new one rather than a reset of this one.
    private var uvcSource = UvcVideoSource(target, ::log)
    private val micSource by lazy { MicrophoneSource() }
    private val stream by lazy { GenericStream(this, this, uvcSource, micSource) }
    private var overlay: ScoreboardOverlay? = null
    private var blackout: BlackoutOverlay? = null
    private var court: CourtOverlay? = null

    /**
     * True while the capture adapter is unplugged.
     *
     * Kept separately from [quality] because it is not a choice: the
     * operator's bandwidth setting is left exactly as they left it,
     * and the court view is forced on top of it until the camera is
     * back. Losing the adapter mid-match - a kicked cable, a phone
     * moved on its tripod - used to end the broadcast, and a YouTube
     * broadcast that ends does not resume: it needs a new one, with a
     * new link, while the match carries on without it.
     */
    private var cameraLost = false

    /**
     * How much bandwidth the stream may use. Loaded from [Settings] in
     * `onCreate` and changed by tapping the rate readout in the header.
     */
    private var quality: StreamQuality = StreamQuality.DEFAULT

    private var wantPreview = false
    private var surfaceReady = false
    private var accessToken: String? = null
    private lateinit var consentLauncher: ActivityResultLauncher<IntentSenderRequest>

    /**
     * Opponent badge picker.
     *
     * Registered in `onCreate` rather than lazily, because
     * `registerForActivityResult` throws once the activity is
     * STARTED -- and the natural place to want this is from a dialog,
     * which is well past that.
     */
    private lateinit var logoPicker: ActivityResultLauncher<String>

    /** Second leg of the badge flow: the crop screen. */
    private lateinit var logoCropper: ActivityResultLauncher<Intent>

    // ---- views ------------------------------------------------------

    private lateinit var surfaceView: SurfaceView
    private lateinit var scoreHomeBig: TextView
    private lateinit var scoreAwayBig: TextView
    private lateinit var homeServeDot: View
    private lateinit var awayServeDot: View
    private lateinit var positionButtons: Map<Int, Button>
    private lateinit var ballServedButton: Button
    private lateinit var undoButton: Button
    private lateinit var gameStartButton: Button
    private lateinit var healthLabel: TextView
    private lateinit var liberoBtn: Button
    private lateinit var timeoutButton: Button
    private lateinit var killButton: Button
    private lateinit var cancelActionButton: Button

    /**
     * Views whose text or colour comes from a team's identity.
     *
     * Held so a rename or recolour repaints them in place. Rebuilding
     * the pane instead would tear down the preview `SurfaceView`,
     * which is bound to the encoder for as long as the stream runs.
     */
    private class TeamChrome {
        var card: View? = null
        var swatch: View? = null
        var cardName: TextView? = null
        var plusOne: Button? = null
        var headerName: TextView? = null
    }

    private val teamChrome =
        mapOf(Side.Home to TeamChrome(), Side.Away to TeamChrome())

    /**
     * Whether a timeout is on: the log's last event is its start.
     *
     * Derived from the log rather than kept as a flag. With the end
     * now recorded too, the log already says whether a timeout is
     * running, and a separate flag could only disagree with it: it
     * reset to false if the activity was recreated mid-timeout, and
     * Undo of an end left the card down over a timeout that was, by
     * the log, still on. It is the LAST event because nothing else
     * can be recorded during one -- [record] closes the timeout before
     * anything else lands.
     *
     * No clock. A volleyball timeout is 30 s on paper, but the
     * operator is watching the court and the referee, not a phone,
     * and a card that vanished on a timer while the teams were still
     * huddled was worse than one that waits for a tap.
     */
    private val timeoutShown: Boolean
        get() = match.events.lastOrNull()?.type == EventType.TimeoutStart

    /**
     * Between sets: true from a Set End until the next ball is served.
     *
     * Derived from the log like [timeoutShown], and for the same
     * reason. Bounded by the serve rather than by a timer because the
     * gap between sets is however long the teams take, and the first
     * serve of the next set is the moment the card stops being true.
     */
    private val setEndShown: Boolean
        get() {
            val marker = match.events.lastOrNull {
                it.type == EventType.SetEnd ||
                    it.type == EventType.BallServed ||
                    it.type == EventType.FirstServe ||
                    it.type == EventType.GameStart ||
                    it.type == EventType.GameEnd
            }
            return marker?.type == EventType.SetEnd
        }
    private var currentTab: Tab = Tab.Events
    /** Rolling upload rate, refreshed by [onNewBitrate]. */
    private var lastBitrateKbps: Long = 0
    private var popup: PopupOverlay? = null
    private var card: CardOverlay? = null
    /** True once prepareVideo/prepareAudio has been called this
     *  activity lifetime. Guards against double-prepare when we do
     *  auto-preview and the operator then hits Start. */
    private var prepared = false
    private lateinit var statusLabel: TextView
    private lateinit var logView: TextView
    private lateinit var startButton: Button
    private lateinit var stopButton: Button
    private lateinit var eventsPane: View
    private lateinit var previewPane: View
    private lateinit var tabToggleBtn: Button

    // ---- lifecycle --------------------------------------------------

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val slug = intent.getStringExtra(EXTRA_SLUG) ?: run { finish(); return }
        store = MatchStore(this)
        match = store.load(slug) ?: run { finish(); return }

        logoPicker = registerForActivityResult(
            ActivityResultContracts.GetContent()
        ) { uri ->
            // Straight into the cropper. A badge off a draw sheet or
            // a club page is almost never square, and the overlays
            // draw it in a square box.
            if (uri != null) logoCropper.launch(LogoCropActivity.intent(this, uri))
        }

        logoCropper = registerForActivityResult(
            ActivityResultContracts.StartActivityForResult()
        ) { result ->
            val path = result.data?.getStringExtra(LogoCropActivity.EXTRA_RESULT_PATH)
            if (result.resultCode == android.app.Activity.RESULT_OK && path != null) {
                storeOpponentLogo(java.io.File(path))
            } else {
                log("logo: cancelled")
            }
        }

        consentLauncher = registerForActivityResult(
            ActivityResultContracts.StartIntentSenderForResult()
        ) { result ->
            YouTubeAuth.handleResult(this, result.data, ::log) { token ->
                accessToken = token
                goLiveWithToken(token)
            }
        }

        val root = buildUi()
        setContentView(root)
        root.applySystemBarInsets()
        // The phone sits on a tripod for two hours and is only
        // touched between rallies. Left alone it sleeps, the activity
        // pauses, and the stream dies with it. Scoped to this window,
        // so Home / Settings still let the phone sleep normally.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        // Both are protected system broadcasts, so NOT_EXPORTED is the
        // right flag even though nothing else can send them.
        ContextCompat.registerReceiver(
            this, usbWatcher,
            IntentFilter().apply {
                addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED)
                addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
            },
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        refreshAllViews()
        log("match: ${match.name} vs ${match.opponent} on ${match.date}")

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.RECORD_AUDIO), 1)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        // Whatever route got us here, the broadcast is over -- do not
        // leave a foreground notification behind for a stream that no
        // longer exists.
        runCatching { unregisterReceiver(usbWatcher) }
        StreamService.stop(this)
        runCatching { if (stream.isStreaming) stream.stopStream() }
        runCatching { if (stream.isOnPreview) stream.stopPreview() }
        runCatching { stream.release() }
    }

    // ---- UI ---------------------------------------------------------

    private fun buildUi(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.BLACK)
        }
        root.addView(buildHeaderBar())

        eventsPane = buildEventsPane()
        previewPane = buildPreviewPane()

        // FrameLayout so both panes exist and we swap visibility;
        // avoids recreating the SurfaceView every tab switch.
        val body = FrameLayout(this)
        body.addView(eventsPane, FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT,
        ))
        body.addView(previewPane, FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT,
        ))
        root.addView(body, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0,
        ).apply { weight = 1f })

        showTab(Tab.Events)
        return root
    }

    private enum class Tab { Events, Preview }

    private fun showTab(t: Tab) {
        // INVISIBLE (not GONE) so both panes stay measured. GONE was
        // making the events pane re-flow taller every time we came
        // back from Preview -- the row heights shifted subtly because
        // measurement pass constraints differed with only one child
        // participating. INVISIBLE keeps both children in the layout
        // pass; only paint changes. Bonus: the SurfaceView surface
        // stays alive across tab switches, so the camera preview
        // does not restart every time.
        eventsPane.visibility = if (t == Tab.Events) View.VISIBLE else View.INVISIBLE
        previewPane.visibility = if (t == Tab.Preview) View.VISIBLE else View.INVISIBLE
        currentTab = t
        // One button, not two. It is labelled with where it *goes*,
        // not where you are -- there are only two panes, so the
        // current one is obvious from what is on screen, and the
        // reclaimed width goes to the stream-health readout.
        tabToggleBtn.text = if (t == Tab.Events) "Preview" else "Events"
        tabToggleBtn.setBackgroundColor(TAB_IDLE_BG)
    }

    private fun buildHeaderBar(): View {
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(Color.parseColor("#0A0A0A"))
            setPadding(8, 4, 8, 4)
            gravity = Gravity.CENTER_VERTICAL
        }
        tabToggleBtn = Button(this).apply {
            text = "Preview"
            setTextColor(Color.WHITE)
            isSingleLine = true
            setOnClickListener {
                showTab(if (currentTab == Tab.Events) Tab.Preview else Tab.Events)
            }
        }
        bar.addView(tabToggleBtn)

        statusLabel = TextView(this).apply {
            text = "idle"
            setTextColor(Color.parseColor("#CCCCCC"))
            textSize = 11f
            setPadding(12, 0, 12, 0)
            // CRITICAL: without single-line, longer status text
            // ("waiting for Google sign-in", "LIVE - streaming")
            // wraps to two lines and drags the whole header bar
            // taller on Start. That is the "top bar expands when
            // I press Start" the operator saw.
            isSingleLine = true
            ellipsize = android.text.TextUtils.TruncateAt.END
        }
        bar.addView(statusLabel, LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f,
        ))

        // Stream health sits where Start was: once you are live,
        // "Start" is a dead control taking prime header space, while
        // the one number that matters -- is the upload actually
        // moving -- had no home at all and was buried in the log.
        quality = Settings.streamQuality(this)
        healthLabel = TextView(this).apply {
            setTextColor(Color.parseColor("#888888"))
            textSize = 11f
            isSingleLine = true
            gravity = Gravity.CENTER
            setPadding(14, 6, 14, 6)
            // Visible before Start too, showing the chosen ceiling
            // rather than a measured rate: the bitrate has to be
            // picked BEFORE `prepareVideo`, so a control that only
            // appeared once live would always be one match late.
            // The chip background is the only hint that it is a
            // button -- plain text in a header bar is not.
            setBackgroundColor(Color.parseColor("#1C1C1C"))
            setOnClickListener { chooseQuality() }
        }
        bar.addView(healthLabel)
        updateHealth()

        startButton = Button(this).apply {
            text = "Start"
            setOnClickListener {
                confirm(
                    "Go live?",
                    "Creates a YouTube broadcast titled\n" +
                    "\"${teamName(Side.Home)} vs ${teamName(Side.Away)}\"\n\n" +
                    "and starts pushing video to it.",
                    "Go live",
                ) { onStartStreaming() }
            }
        }
        stopButton = Button(this).apply {
            text = "Stop"
            isEnabled = false
            setOnClickListener {
                confirm(
                    "Stop streaming?",
                    "The YouTube broadcast ends and cannot be resumed " +
                    "into the same video.\n\n" +
                    "The match stays open and scoring keeps working.",
                    "Stop",
                ) { onStopStreaming() }
            }
        }
        bar.addView(startButton)
        bar.addView(stopButton)
        // Export moved to the match list on Home: it is an
        // after-the-fact action, sometimes days later, and it was
        // taking header room from the live controls.
        // Belt-and-braces: pin the bar to a fixed pixel height so
        // even if a child View somehow decides to wrap, the bar
        // itself does not grow.
        val density = resources.displayMetrics.density
        bar.layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            (56 * density).toInt(),
        )
        return bar
    }

    private fun buildPreviewPane(): View {
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        surfaceView = SurfaceView(this)
        surfaceView.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(holder: SurfaceHolder) {
                surfaceReady = true
                // Auto-preview: the camera should show as soon as
                // there is a surface to draw into, whether or not
                // Start has been pressed. Lets the operator frame
                // the shot before going live.
                if (ensurePrepared() && !stream.isOnPreview) attachPreview()
            }
            override fun surfaceChanged(h: SurfaceHolder, f: Int, w: Int, hgt: Int) {
                if (stream.isOnPreview) stream.getGlInterface().setPreviewResolution(w, hgt)
            }
            override fun surfaceDestroyed(holder: SurfaceHolder) {
                surfaceReady = false
                // While live, let the preview go but keep the stream.
                //
                // This was the bug behind "the phone goes to sleep
                // and YouTube drops": the screen turning off destroys
                // the SurfaceView's surface, and stopping the preview
                // here tore down the GL pipeline the encoder is
                // drawing through. The broadcast died with the
                // screen, which no wake lock could have prevented
                // because nothing was asleep -- the app had switched
                // itself off.
                //
                // The preview is only a monitor. `startStream`
                // renders to the encoder's own surface, so dropping
                // the preview is a no-op for what YouTube receives,
                // and `attachPreview` puts it back when the surface
                // returns.
                if (stream.isStreaming) {
                    log("preview surface gone - stream continues")
                    return
                }
                if (stream.isOnPreview) stream.stopPreview()
            }
        })
        col.addView(surfaceView, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0,
        ).apply { weight = 3f })

        logView = TextView(this).apply {
            setTextColor(Color.parseColor("#8FE38F"))
            textSize = 10f
            typeface = Typeface.MONOSPACE
            movementMethod = ScrollingMovementMethod()
            setPadding(8, 4, 8, 4)
        }
        col.addView(ScrollView(this).apply { addView(logView) },
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0,
            ).apply { weight = 1f })
        return col
    }

    private fun buildEventsPane(): View {
        val scroll = ScrollView(this).apply { setBackgroundColor(Color.BLACK) }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(12, 12, 12, 24)
        }
        col.addView(buildScoreHeader())
        col.addView(spacer(8))
        col.addView(buildHomeCard())
        col.addView(spacer(8))
        col.addView(buildAwayCard())
        col.addView(spacer(8))
        col.addView(buildMatchSection())
        scroll.addView(col)
        return scroll
    }

    /**
     * Big score readout across the top. Team names appear in their
     * accent colour; scores are big and white. Repainted on every
     * event via `refreshAllViews`.
     */
    private fun buildScoreHeader(): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(Color.parseColor("#111111"))
            // Tighter than the original 16/12: courtside operators
            // want the score header slim so the rotation grid and
            // player-tag buttons stay above the fold on portrait.
            setPadding(12, 4, 12, 4)
            gravity = Gravity.CENTER_VERTICAL
        }

        val homeCol = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
        }
        homeCol.addView(TextView(this).apply {
            teamChrome.getValue(Side.Home).headerName = this
            text = match.homeRoster.teamName.ifBlank { match.team }.uppercase()
            setTextColor(parseColor(match.homeRoster.teamColor, DEFAULT_HOME_COLOR))
            textSize = 12f
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
            // Truncate long club names with an ellipsis instead of
            // wrapping to a second line -- word-breaking a team
            // name like "Whitecaps" looked broken and shifted the
            // score header height.
            isSingleLine = true
            ellipsize = android.text.TextUtils.TruncateAt.END
        })
        scoreHomeBig = TextView(this).apply {
            text = "0"
            setTextColor(Color.WHITE)
            textSize = 32f
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
            includeFontPadding = false
        }
        homeCol.addView(scoreHomeBig)
        row.addView(homeCol, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))

        // Between the two big scores we stack a subtle dash and an
        // Undo button. Undo rolls back the very last event -- the
        // fastest recovery from a mis-tap without leaving the
        // scoring surface. Disabled when the event list is empty.
        val midCol = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
        }
        midCol.addView(TextView(this).apply {
            text = "-"
            setTextColor(Color.parseColor("#666666"))
            textSize = 20f
            gravity = Gravity.CENTER
            includeFontPadding = false
        })
        // Undo uses `compactButton` -- default Button minHeight of
        // 48dp was making the whole score-header row 48dp+padding
        // tall regardless of the score font size.
        undoButton = compactButton("Undo") { onUndo() }
        midCol.addView(undoButton)
        row.addView(midCol, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply { setMargins(16, 0, 16, 0) })

        val awayCol = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
        }
        awayCol.addView(TextView(this).apply {
            teamChrome.getValue(Side.Away).headerName = this
            text = match.opponentRoster.teamName.ifBlank { match.opponent }.uppercase()
            setTextColor(parseColor(match.opponentRoster.teamColor, DEFAULT_AWAY_COLOR))
            textSize = 12f
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
            isSingleLine = true
            ellipsize = android.text.TextUtils.TruncateAt.END
        })
        scoreAwayBig = TextView(this).apply {
            text = "0"
            setTextColor(Color.WHITE)
            textSize = 32f
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
            includeFontPadding = false
        }
        awayCol.addView(scoreAwayBig)
        row.addView(awayCol, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        return row
    }

    /**
     * Home team card: coloured border, name row with Sub/-1/+1, the
     * 6-slot rotation grid, and every player-attributed tag.
     */
    private fun buildHomeCard(): View {
        val card = cardContainer(match.homeRoster.teamColor, DEFAULT_HOME_COLOR)
        teamChrome.getValue(Side.Home).card = card
        card.addView(buildTeamHeaderRow(
            name = match.homeRoster.teamName.ifBlank { match.team },
            colorHex = match.homeRoster.teamColor ?: DEFAULT_HOME_COLOR,
            side = Side.Home,
            isHomeCard = true,
        ))
        card.addView(spacer(10))
        card.addView(buildPositionGrid())
        card.addView(spacer(10))
        // Player actions: home only, per the reference layout. Two
        // columns matching the screenshot.
        // Emoji first, word second. At courtside speed the glyph is
        // what the eye lands on; the word is there for the tap after,
        // when the operator is checking rather than reaching. Colour
        // groups them: attack, defence, everything else.
        card.addView(rowOf(
            playerActionButton(LBL_KILL, EventType.Kill, BG_ATTACK)
                .also { killButton = it },
            playerActionButton("\uD83C\uDFAF Ace", EventType.Ace, BG_ATTACK),
        ))
        card.addView(rowOf(
            playerActionButton("\uD83E\uDD32 Dig", EventType.Dig, BG_DEFENCE),
            playerActionButton("\uD83E\uDD3F Dive", EventType.Dive, BG_DEFENCE),
        ))
        card.addView(rowOf(
            playerActionButton("\uD83E\uDDF1 Block", EventType.Block, BG_DEFENCE),
            playerActionButton("\u2B50 Highlight", EventType.Highlight, BG_EXTRA),
        ))
        card.addView(buildCancelActionButton(), LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = 10 })
        return card
    }

    /**
     * The way out of armed mode. Hidden until something is armed.
     *
     * Tapping the armed action again has always disarmed, but that is
     * knowledge, not a control: courtside, with a rally starting, the
     * operator who armed the wrong thing was looking for a way out and
     * there was nothing on screen that said so.
     *
     * Deliberately NOT [gated]: armed mode disables every gated button,
     * and the escape hatch cannot be one of the things that goes dark.
     * It sits under the action buttons, the length of the card away
     * from the six slots, so reaching for it cannot credit a player.
     */
    private fun buildCancelActionButton(): Button = Button(this).apply {
        text = LBL_CANCEL
        setTextColor(Color.WHITE)
        background = buttonFill(BG_CANCEL)
        visibility = View.GONE
        setOnClickListener { cancelArmed() }
        cancelActionButton = this
    }

    /**
     * Back out of the armed action.
     *
     * During the assist prompt this cancels only the assist: the kill
     * is already recorded and is not in question, so the tap means
     * "nobody set that one up" -- the same thing tapping the killer
     * means, for an operator whose eye is on the buttons rather than
     * the court.
     */
    private fun cancelArmed() {
        val armed = armedAction ?: return
        // Abandoned, so its clock goes with it - otherwise the next
        // event recorded would inherit this one's start.
        actionStartedAt = null
        if (armed == EventType.Assist) {
            assistForKiller = null
            log("assist: none")
        } else {
            log("cancelled: $armedLabel")
        }
        disarm()
    }

    private fun buildAwayCard(): View {
        val card = cardContainer(match.opponentRoster.teamColor, DEFAULT_AWAY_COLOR)
        teamChrome.getValue(Side.Away).card = card
        card.addView(buildTeamHeaderRow(
            name = match.opponentRoster.teamName.ifBlank { match.opponent },
            colorHex = match.opponentRoster.teamColor ?: DEFAULT_AWAY_COLOR,
            side = Side.Away,
            isHomeCard = false,
        ))
        // The opponent is named at match setup, often from a draw
        // sheet that turns out to be wrong, so the card itself is the
        // way to fix it. The buttons inside the header consume their
        // own taps; anywhere else on the card opens the editor.
        //
        // The home card deliberately has no equivalent: its name and
        // colour come from the team roster imported from VME, and
        // editing a local copy here would silently diverge from the
        // record the editor re-reads on import.
        card.isClickable = true
        card.setOnClickListener { editTeam(Side.Away) }
        return card
    }

    /**
     * Rename / recolour a team.
     *
     * Colours are a fixed row of swatches rather than a picker: the
     * value has to land as a hex string VME will accept, and eight
     * jersey-ish choices cover what a scoreboard needs while staying
     * tappable with one thumb.
     */
    private fun editTeam(side: Side) {
        val roster = if (side == Side.Home) match.homeRoster else match.opponentRoster
        val fallback = if (side == Side.Home) DEFAULT_HOME_COLOR else DEFAULT_AWAY_COLOR
        var picked = roster.teamColor ?: fallback

        val input = EditText(this).apply {
            setText(roster.teamName.ifBlank {
                if (side == Side.Home) match.team else match.opponent
            })
            hint = "Team name"
            isSingleLine = true
        }

        val cells = TEAM_COLORS.map { hex -> hex to View(this) }
        // A ring marks the current choice rather than a tick, which
        // would disappear into the lighter swatches. The ring's own
        // colour flips with the swatch's luminance for the same
        // reason: white-on-white is not an indicator.
        fun paint() {
            for ((hex, cell) in cells) {
                val color = Color.parseColor(hex)
                val selected = hex.equals(picked, ignoreCase = true)
                cell.background = roundedFill(
                    color,
                    strokePx = if (selected) 7 else 1,
                    strokeColor = when {
                        !selected -> Color.parseColor("#33FFFFFF")
                        androidx.core.graphics.ColorUtils.calculateLuminance(color) > 0.55 ->
                            Color.BLACK
                        else -> Color.WHITE
                    },
                )
            }
        }

        // A grid, not a row: at this many colours a single row gives
        // swatches too narrow to hit, and the eye cannot find a
        // shade in a long strip anyway. Rows are hues, so scanning
        // down picks the family and across picks the shade.
        val swatches = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        cells.chunked(SWATCHES_PER_ROW).forEach { chunk ->
            val line = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
            }
            chunk.forEachIndexed { i, (hex, cell) ->
                cell.setOnClickListener { picked = hex; paint() }
                line.addView(cell, LinearLayout.LayoutParams(0, 88, 1f).apply {
                    if (i < chunk.size - 1) marginEnd = 8
                })
            }
            swatches.addView(line, LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 8 })
        }
        paint()

        val body = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 8)
            addView(input)
            addView(spacer(24))
            addView(swatches)
        }

        AlertDialog.Builder(this)
            .setTitle(if (side == Side.Home) "Home team" else "Opponent")
            .setView(body)
            .setPositiveButton("Save") { _, _ ->
                saveTeam(side, input.text.toString().trim(), picked)
            }
            // Commits the name and colour on the way out, so opening
            // the picker is not a silent way to lose them.
            .setNeutralButton("Logo...") { _, _ ->
                saveTeam(side, input.text.toString().trim(), picked)
                logoPicker.launch("image/*")
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun saveTeam(side: Side, name: String, colorHex: String) {
        val roster = if (side == Side.Home) match.homeRoster else match.opponentRoster
        val updated = roster.copy(teamName = name, teamColor = colorHex)
        match = if (side == Side.Home) match.copy(homeRoster = updated)
                else match.copy(opponentRoster = updated)
        store.save(match)
        refreshAllViews()
        // The scoreboard and the centre card carry the name and the
        // colour, so they are stale until repainted.
        rebuildOverlayChrome()
        log("team: ${side.wire} -> ${name.ifBlank { "(unnamed)" }} $colorHex")
    }

    /**
     * Copy the cropped image into the cache as the opponent's badge.
     *
     * Copied rather than referenced: the crop lands in `cacheDir`,
     * which Android is free to clear whenever it wants the space, so
     * a match reopened tomorrow would find nothing there. The same
     * went for the `content://` URI this started from -- a grant to
     * this task, not a durable handle. The overlays want a plain
     * file under `filesDir` either way.
     *
     * Home badges do not come through here. Theirs is imported from
     * VME with the roster, which is the copy the editor already has
     * and will re-read on import.
     */
    private fun storeOpponentLogo(cropped: java.io.File) {
        val bytes = runCatching { cropped.readBytes() }.getOrNull()
        if (bytes == null || bytes.isEmpty()) {
            log("logo: could not read the cropped image")
            return
        }
        runCatching { cropped.delete() }
        val path = PhotoCache.storeLogo(
            this, PhotoCache.opponentKey(match.slug), bytes,
        )
        if (path == null) {
            log("logo: could not save (disk full?)")
            return
        }
        match = match.copy(
            opponentRoster = match.opponentRoster.copy(localLogoPath = path),
        )
        store.save(match)
        log("logo: opponent badge saved (${bytes.size / 1024} KB)")
        rebuildOverlayChrome()
    }

    /** A team's cached badge, decoded for overlay use, or null. */
    private fun logoFor(side: Side): android.graphics.Bitmap? = PhotoCache.load(
        (if (side == Side.Home) match.homeRoster else match.opponentRoster).localLogoPath,
        LOGO_PX,
    )

    /**
     * Push a team rename or recolour into the live overlays.
     *
     * Split because the two overlays can take different amounts of
     * it: the centre card redraws names and colours freely, while
     * the scoreboard can only take the colours (its bar width is
     * measured from the names and frozen at attach -- see
     * [ScoreboardOverlay.setTeamColors]).
     *
     * Before the stream starts there is nothing to push: both
     * overlays are built at Start, so they pick the edit up for
     * free. That is also the case that actually happens -- the
     * opponent gets named off a draw sheet during warm-up.
     */
    private fun rebuildOverlayChrome() {
        val homeFill = parseColor(match.homeRoster.teamColor, DEFAULT_HOME_COLOR)
        val awayFill = parseColor(match.opponentRoster.teamColor, DEFAULT_AWAY_COLOR)
        card?.setTeams(
            teamName(Side.Home), teamName(Side.Away), homeFill, awayFill,
            logoFor(Side.Home), logoFor(Side.Away),
        )
        overlay?.let { ov ->
            ov.setTeamColors(homeFill, awayFill)
            if (ov.homeName != teamName(Side.Home) || ov.awayName != teamName(Side.Away)) {
                log("team: scoreboard name changes at the next stream start")
            }
            // Same reason as the name: the bar's sections are sized
            // around the badge in `init`, so a logo added mid-stream
            // has nowhere to go until the bar is rebuilt.
            log("team: scoreboard badge changes at the next stream start")
        }
        pushOverlay()
    }

    /**
     * Repaint every view whose text or colour comes from a team.
     *
     * Cheap enough to run on every refresh -- five view writes per
     * side -- and running it unconditionally means a rename cannot
     * leave one label behind.
     */
    private fun refreshTeamChrome() {
        for (side in listOf(Side.Home, Side.Away)) {
            val c = teamChrome[side] ?: continue
            val roster = if (side == Side.Home) match.homeRoster else match.opponentRoster
            val fallback = if (side == Side.Home) DEFAULT_HOME_COLOR else DEFAULT_AWAY_COLOR
            val raw = roster.teamName.ifBlank {
                if (side == Side.Home) match.team else match.opponent
            }
            val color = parseColor(roster.teamColor, fallback)

            c.cardName?.text = raw
            c.headerName?.let {
                it.text = raw.uppercase()
                it.setTextColor(color)
            }
            c.swatch?.setBackgroundColor(color)
            (c.card?.background as? android.graphics.drawable.GradientDrawable)
                ?.setStroke(3, color)
            c.plusOne?.let {
                it.background = buttonFill(color)
                // Keep the armed-mode restore in step: it hands back
                // whatever was captured the first time the button was
                // tinted, which is now the wrong colour.
                if (defaultBackgrounds.containsKey(it)) {
                    defaultBackgrounds[it] = it.background
                }
            }
        }
    }

    private fun cardContainer(colorHex: String?, fallback: String) = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(12, 10, 12, 10)
        // Coloured border via GradientDrawable -- cheapest correct
        // way to get a rounded stroke without an XML drawable.
        background = android.graphics.drawable.GradientDrawable().apply {
            setColor(Color.parseColor("#111111"))
            setStroke(3, parseColor(colorHex, fallback))
            cornerRadius = 12f
        }
    }

    /** Team name + serving dot + [Sub (home only)] + [-1] + [+1]. */
    private fun buildTeamHeaderRow(
        name: String, colorHex: String, side: Side, isHomeCard: Boolean,
    ): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        // Coloured square marker (matches the reference).
        val chrome = teamChrome.getValue(side)
        row.addView(View(this).apply {
            chrome.swatch = this
            setBackgroundColor(parseColor(colorHex, "#FFFFFF"))
        }, LinearLayout.LayoutParams(24, 24).apply { marginEnd = 8 })

        row.addView(TextView(this).apply {
            chrome.cardName = this
            text = name
            setTextColor(Color.WHITE)
            textSize = 18f
            typeface = Typeface.DEFAULT_BOLD
            // Same reasoning as the score header: truncate rather
            // than break mid-word.
            isSingleLine = true
            ellipsize = android.text.TextUtils.TruncateAt.END
        }, LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f,
        ))

        // Serving indicator: filled amber circle; visibility flipped by
        // refreshAllViews.
        val dot = View(this).apply {
            background = android.graphics.drawable.GradientDrawable().apply {
                shape = android.graphics.drawable.GradientDrawable.OVAL
                setColor(Color.parseColor("#FFD54F"))
            }
            visibility = View.INVISIBLE
        }
        if (isHomeCard) homeServeDot = dot else awayServeDot = dot
        row.addView(dot, LinearLayout.LayoutParams(18, 18).apply { marginEnd = 8 })

        // The "Sub" button was removed: tapping a position slot in
        // the rotation grid opens the same jersey picker directly.
        // The volleyball emoji is a first-serve pick; kept small
        // via `compactButton` so it does not steal room from +1.
        row.addView(gated(compactButton("\uD83C\uDFD0") {
            record(EventType.FirstServe, mapOf("team" to side.wire))
        }), LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply { marginEnd = gapPx })
        row.addView(gated(compactButton("-1") { onMinusOne(side) }),
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { marginEnd = gapPx })
        row.addView(gated(Button(this).apply {
            chrome.plusOne = this
            text = "+1"
            background = buttonFill(parseColor(colorHex, "#3F7EFF"))
            setTextColor(Color.WHITE)
            setOnClickListener { onPlusOne(side) }
        }))
        return row
    }

    /**
     * VME's rotation grid: P4/P3/P2 on top, P5/P6/P1 on bottom --
     * reads the way the operator sees the court from behind. A tap
     * opens the substitution dialog for that position, so the "Sub"
     * button and the per-slot tap both end up in the same flow.
     */
    private fun buildPositionGrid(): View {
        val grid = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val top = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        val bot = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        val buttons = mutableMapOf<Int, Button>()

        // Gaps here are also what round 14 asked for as "borders
        // between buttons in player select mode": when the grid is
        // armed, each lit slot has to read as its own target, and a
        // continuous green band does not.
        fun fill(into: LinearLayout, order: List<Int>) {
            order.forEachIndexed { i, pos ->
                val b = positionButton(pos)
                buttons[pos] = b
                into.addView(b, LinearLayout.LayoutParams(
                    0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f,
                ).apply { if (i < order.size - 1) marginEnd = gapPx })
            }
        }
        fill(top, listOf(4, 3, 2))
        fill(bot, listOf(5, 6, 1))
        positionButtons = buttons
        grid.addView(top, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply { bottomMargin = gapPx })
        grid.addView(bot)
        return grid
    }

    private fun positionButton(pos: Int): Button = gated(Button(this).apply {
        text = positionLabel(pos, null)
        textSize = 12f
        setPadding(6, 8, 6, 8)
        // Same rounded fill as every other control, or the grid
        // reads as stock Android dropped into a themed screen.
        setTextColor(Color.WHITE)
        background = buttonFill(BG_SLOT)
        setOnClickListener { onPositionTap(pos) }
        // Long-press still opens the full roster, so a bench player
        // can be credited without first subbing them on.
        setOnLongClickListener { onPositionLongPress(pos); true }
    })

    /** A grid tap means "this player" when an action is armed, and
     *  "substitute into this slot" otherwise. */
    private fun onPositionTap(pos: Int) {
        val armed = armedAction
        if (armed == null) { subForPosition(pos); return }
        val jersey = gameState.homePositions[pos]
        if (jersey == null) {
            // Empty slot cannot be credited with anything. Say so
            // rather than silently doing nothing.
            log("$armedLabel: P$pos is empty")
            return
        }
        // The assist prompt is dismissed by tapping the killer: they
        // cannot have assisted themselves, so the tap has no other
        // meaning, and it needs no button of its own on a screen that
        // has no room for one.
        if (armed == EventType.Assist && jersey == assistForKiller) {
            assistForKiller = null
            disarm()
            log("assist: none")
            return
        }
        disarm()
        // The prompt is answered either way it ends, so the pending
        // killer goes with it.
        if (armed == EventType.Assist) assistForKiller = null
        record(armed, mapOf("team" to Side.Home.wire, "player_number" to jersey))
        askForAssist(armed, jersey)
    }

    /** Long-press: credit a player who is not on the court (armed),
     *  or substitute (idle). Both go through the roster picker. */
    private fun onPositionLongPress(pos: Int) {
        val armed = armedAction
        if (armed == null) { subForPosition(pos); return }
        disarm()
        if (armed == EventType.Assist) assistForKiller = null
        pickPlayer(Side.Home, excludeOnCourt = false) { jersey ->
            record(armed, mapOf("team" to Side.Home.wire, "player_number" to jersey))
            askForAssist(armed, jersey)
        }
    }

    /** Narrow-but-tappable button used in the team header row (first
     *  serve, -1). Zeroed `minWidth` so a two-character label does
     *  not eat 88dp of the row, but the default 48dp min-height
     *  stays so the button is still tap-friendly at courtside speed.
     *  Default Button `textSize` (14sp) kept -- shrinking it made
     *  the buttons unreadable. Horizontal padding is roughly 2x
     *  the previous value so the -1 and first-serve buttons have
     *  a comfortable target area. */
    private fun compactButton(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        isSingleLine = true
        minWidth = 0
        minimumWidth = 0
        setPadding(36, 8, 36, 8)
        setTextColor(Color.WHITE)
        background = buttonFill(BG_NEUTRAL)
        setOnClickListener { onClick() }
    }

    /**
     * MATCH section. Score Fix / Message / Focus In/Out from earlier
     * revisions are gone -- Score Fix is replaced by the -1 buttons
     * on team cards; the others were never used courtside per the
     * reference layout.
     */
    private fun buildMatchSection(): View {
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        col.addView(sectionHeader("MATCH"))

        // Ball Served is the most-pressed button in the app -- once
        // per rally, under time pressure, while the operator is
        // looking at the court rather than the phone. It gets the
        // full width and extra height so it can be hit without
        // aiming. Everything else in this section is occasional.
        ballServedButton = gated(simpleButton(LBL_BALL_SERVED, BG_SERVE) {
            record(EventType.BallServed)
        }).apply {
            textSize = 18f
            val d = resources.displayMetrics.density
            minimumHeight = (72 * d).toInt()
            minHeight = (72 * d).toInt()
        }
        col.addView(ballServedButton, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply { bottomMargin = gapPx })

        // Tapping Timeout while one is running ends it rather than
        // starting a second: the card is up, the operator can see it,
        // and "stop this" is the only thing the button can usefully
        // mean in that state.
        timeoutButton = gated(simpleButton(LBL_TIMEOUT, BG_NEUTRAL) {
            record(if (timeoutShown) EventType.TimeoutEnd else EventType.TimeoutStart)
        })
        col.addView(rowOf(
            gated(simpleButton("\uD83D\uDD04 Replay", BG_NEUTRAL) {
                record(EventType.Replay)
            }),
            timeoutButton,
        ))

        // Game Start is the one control that must stay live before
        // the game begins -- it is what opens the gate.
        gameStartButton = simpleButton("\u25B6\uFE0F Game Start", BG_LIFECYCLE) {
            record(EventType.GameStart)
        }
        col.addView(rowOf(
            gameStartButton,
            gated(simpleButton("\u23F9\uFE0F Game End", BG_LIFECYCLE) { confirmGameEnd() }),
            gated(simpleButton("\uD83C\uDFC1 End Set", BG_LIFECYCLE) { confirmSetEnd() }),
        ))

        col.addView(spacer(16))
        col.addView(liberoButton())
        return col
    }

    private fun rowOf(vararg views: View): LinearLayout {
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        views.forEachIndexed { i, v ->
            row.addView(v, LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f,
            ).apply { if (i < views.size - 1) marginEnd = gapPx })
        }
        // Set here rather than at each call site: `addView(child)`
        // keeps the child's own params when it has them, so every
        // caller gets the gap under the row without passing anything.
        row.layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply { bottomMargin = gapPx }
        return row
    }

    private fun sectionHeader(text: String) = TextView(this).apply {
        this.text = text
        setTextColor(Color.parseColor("#8FE38F"))
        textSize = 12f
        typeface = Typeface.DEFAULT_BOLD
        setPadding(0, 16, 0, 6)
    }

    /**
     * Libero picker, parked at the bottom of the events pane.
     *
     * Set once at the start of a match and then never touched, so
     * it sits below the controls that get used every rally rather
     * than competing with them.
     */
    private fun liberoButton(): View = simpleButton(
        "\uD83E\uDDBA Liberos...", BG_LIBERO,
    ) { pickLiberos() }.also { liberoBtn = it }

    /**
     * Rounded fill used for every coloured surface built in code.
     * The one place `GradientDrawable` is named, so the rest of this
     * file stays readable.
     */
    private fun roundedFill(
        color: Int, strokePx: Int = 0, strokeColor: Int = Color.TRANSPARENT,
    ) = android.graphics.drawable.GradientDrawable().apply {
        setColor(color)
        cornerRadius = 10f
        if (strokePx > 0) setStroke(strokePx, strokeColor)
    }

    /**
     * Button background that keeps its pressed feedback.
     *
     * Round 13 established that `setBackgroundColor` *replaces* the
     * whole background drawable, taking the pressed state with it and
     * leaving a flat shape that reads as disabled. A ripple over a
     * rounded fill gives each group of controls its own colour and
     * keeps the tap feedback. Disabled still reads correctly because
     * the gate dims with `alpha`, not with the drawable.
     */
    private fun buttonFill(color: Int) = android.graphics.drawable.RippleDrawable(
        android.content.res.ColorStateList.valueOf(Color.parseColor("#40FFFFFF")),
        roundedFill(color),
        null,
    )

    private fun simpleButton(
        label: String, tint: Int = BG_NEUTRAL, onClick: () -> Unit,
    ) = Button(this).apply {
        text = label
        setTextColor(Color.WHITE)
        background = buttonFill(tint)
        setOnClickListener { onClick() }
    }

    /**
     * Confirmation for a tap that cannot be shrugged off.
     *
     * Undo rolls any of these back, but a set end clears the rotation
     * and a game end stops scoring and changes what is on the video
     * -- noticing the mis-tap is the slow part, not repairing it. The
     * message carries the score so the operator checks the thing they
     * are committing to.
     */
    private fun confirm(
        title: String, message: String, positive: String, onYes: () -> Unit,
    ) {
        AlertDialog.Builder(this)
            .setTitle(title)
            .setMessage(message)
            .setPositiveButton(positive) { _, _ -> onYes() }
            .setNegativeButton("Cancel", null)
            .show()
    }

    /**
     * Every control that only makes sense once the game is under way.
     *
     * Registered here rather than gated individually so that adding a
     * new scoring button cannot silently forget the gate -- if it is
     * built with [gated] it is covered.
     *
     * Deliberately NOT in this list: Game Start (the thing that opens
     * the gate), the stream controls in the header bar, Export, and
     * the pop-up test buttons.
     */
    private val gatedButtons = mutableListOf<Button>()

    /** Player-attribution buttons (Kill, Dig, ...). Held separately
     *  so the armed one can be lit while the rest go dark. */
    private val actionButtons = mutableListOf<Button>()

    /**
     * Stock background drawables, captured the first time a button
     * is tinted.
     *
     * `setBackgroundColor` does not tint a Button -- it *replaces*
     * the whole background drawable, including the state list that
     * gives it its raised fill and its pressed/disabled states. So
     * "clear the tint" cannot be `setBackgroundColor(TRANSPARENT)`:
     * that leaves a flat, unfilled button that reads as disabled
     * even though it is perfectly clickable. The only way back is
     * to put the original drawable return.
     */
    private val defaultBackgrounds =
        HashMap<Button, android.graphics.drawable.Drawable?>()

    private fun tintButton(b: Button, color: Int) {
        if (!defaultBackgrounds.containsKey(b)) defaultBackgrounds[b] = b.background
        // `buttonFill`, not `setBackgroundColor`: the armed slot has
        // to keep the rounded shape it had a moment ago, or arming
        // an action visibly squares off half the screen.
        b.background = buttonFill(color)
    }

    private fun restoreButton(b: Button) {
        // containsKey, not a null check: a button that was never
        // tinted must be left alone, and one whose stock background
        // genuinely was null must still be restorable to null.
        if (!defaultBackgrounds.containsKey(b)) return
        b.background = defaultBackgrounds[b]
    }

    /** Mark a button as game-only. Returns it, so it can be used
     *  inline where the button is added to a row. */
    private fun <T : Button> gated(b: T): T {
        gatedButtons.add(b)
        return b
    }

    /**
     * Home-only player-attributed tag. Arms the rotation grid rather
     * than opening a dialog -- see [armAction].
     *
     * Ace is the exception: it is attributed automatically, because
     * by definition the server scored it.
     */
    private fun playerActionButton(
        label: String, type: EventType, tint: Int = BG_ATTACK,
    ) = gated(Button(this).apply {
        text = label
        setTextColor(Color.WHITE)
        background = buttonFill(tint)
        setOnClickListener {
            when {
                // The Kill button wears the Assist label during the
                // follow-up prompt, and a tap on it there used to arm
                // Kill again -- so the operator who pressed the lit
                // button expecting "now pick the setter" got a second
                // kill armed instead, and had to tap their way back out
                // of a loop while the next rally started. Here it means
                // what the Cancel button beside it means: no assist.
                armedAction == EventType.Assist && type == EventType.Kill ->
                    cancelArmed()
                type == EventType.Ace -> recordAce()
                else -> armAction(type, label)
            }
        }
        actionButtons.add(this)
    })

    // ---- armed-action mode ------------------------------------------

    /** Non-null while waiting for the operator to tap the player a
     *  pending action belongs to. */
    private var armedAction: EventType? = null
    private var armedLabel: String = ""

    /**
     * Jersey of the player whose kill is waiting for an assist, or
     * null when no such prompt is open.
     *
     * A kill is nearly always set up by someone, and the set is worth
     * recording - but it is a second tap at the moment the next rally
     * is starting, so it has to be dismissable without thinking. The
     * killer's own slot does that: nobody assists their own kill, so
     * tapping it can only mean "there was no assist".
     */
    private var assistForKiller: Int? = null

    /**
     * Arm [type] and wait for a tap on the rotation grid.
     *
     * Replaces the old modal jersey list. Two reasons the dialog was
     * wrong courtside:
     *
     * - It covered the score and the grid at the exact moment the
     *   next rally is starting. The operator had to look away from
     *   the court to read a list.
     * - The list was the *whole roster* in jersey order, but the
     *   player who just got the kill is almost always one of the six
     *   on court -- and their spot on screen matches where the
     *   operator just saw them standing. Tapping that is close to
     *   muscle memory; finding a row in a sorted list is not.
     *
     * While armed, every other control is disabled and the six grid
     * slots are highlighted, so there is exactly one legal next tap.
     * Tapping the same action button again disarms, so a mis-tap is
     * backed out with the same button that caused it.
     */
    private fun armAction(type: EventType, label: String) {
        // Arming anything by hand abandons a pending assist prompt:
        // the operator has moved on to another action.
        if (assistForKiller != null && type != EventType.Assist) {
            assistForKiller = null
        }
        // Tapping the armed action again is a cancel, so its clock
        // goes too.
        if (armedAction == type) { actionStartedAt = null; disarm(); return }
        // The tap that arms is the tap that counts; the one that picks
        // the player comes seconds later.
        beginAction()
        armedAction = type
        armedLabel = label
        refreshAllViews()
        log("armed: $label - tap the player")
    }

    // ---- liberos ----------------------------------------------------

    /**
     * Multi-select over the home roster.
     *
     * A match has one or two liberos, chosen before first serve and
     * fixed for the set, so a checklist is the right shape -- unlike
     * the per-event pickers, this is a deliberate setup step with no
     * time pressure.
     */
    private fun pickLiberos() {
        val players = match.homeRoster.players.sortedBy { it.number }
        if (players.isEmpty()) {
            log("liberos: roster is empty")
            return
        }
        val labels = players
            .map { "#${it.number}  ${it.shortName ?: it.name}" }
            .toTypedArray()
        val checked = players.map { it.number in match.liberos }.toBooleanArray()
        AlertDialog.Builder(this)
            .setTitle("Liberos")
            .setMultiChoiceItems(labels, checked) { _, which, isChecked ->
                checked[which] = isChecked
            }
            .setPositiveButton("Save") { _, _ ->
                val chosen = players
                    .filterIndexed { i, _ -> checked[i] }
                    .map { it.number }
                    .toSet()
                match = match.copy(liberos = chosen)
                store.save(match)
                refreshAllViews()
                val names = chosen.joinToString(", ") { "#$it" }
                log("liberos: ${names.ifBlank { "none" }}")
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun disarm() {
        if (armedAction == null) return
        armedAction = null
        armedLabel = ""
        refreshAllViews()
    }

    /**
     * A kill was just credited - ask who set it up.
     *
     * Arms the grid a second time rather than opening a dialog, so the
     * tap is in the same place and the same shape as the one that just
     * recorded the kill. Tapping the killer means "no assist" (see
     * [assistForKiller]); tapping nothing at all leaves the prompt
     * armed, and the next action button disarms it.
     */
    private fun askForAssist(justRecorded: EventType, killer: Int) {
        if (justRecorded != EventType.Kill) return
        assistForKiller = killer
        // The set happened at the kill, not when the operator got round
        // to naming who made it.
        beginAction()
        armedAction = EventType.Assist
        armedLabel = LBL_ASSIST
        refreshAllViews()
        log("assist: tap the setter, or #$killer for none")
    }

    /**
     * An Ace needs no player prompt: the serving player scored it.
     * Asking would be busywork, and the answer is already on screen
     * in position 1.
     *
     * Falls back to the picker only when the rotation is not filled
     * in, which is the one case where we genuinely cannot know.
     */
    private fun recordAce() {
        beginAction()
        val server = gameState.homePositions[1]
        if (server == null) {
            log("ace: no player in P1 - pick manually")
            pickPlayer(Side.Home, excludeOnCourt = false) { jersey ->
                record(EventType.Ace, mapOf(
                    "team" to Side.Home.wire, "player_number" to jersey,
                ))
            }
            return
        }
        record(EventType.Ace, mapOf(
            "team" to Side.Home.wire, "player_number" to server,
        ))
    }

    /**
     * Three lines for a rotation slot: the position, then the jersey
     * number on a line of its own at double size, then the name.
     *
     * The number is what the operator aims at. It is how the scorer
     * calls a player out ("twelve got that one"), how the roster is
     * ordered, and the only label that is unambiguous at arm's length
     * in a noisy hall -- so it gets a line to itself and twice the
     * height, and the position and name become captions around it.
     *
     * Always three lines, empty slot included, so the grid does not
     * change height on a substitution and shift a button out from
     * under a thumb already moving towards it.
     */
    private fun positionLabel(pos: Int, jersey: Int?): CharSequence {
        val player = jersey?.let { j ->
            match.homeRoster.players.firstOrNull { it.number == j }
        }
        val big = jersey?.toString() ?: "–"
        // The libero marker rides on the number's line but stays at
        // caption size: it qualifies the number, it is not part of it.
        val suffix = if (jersey != null && jersey in match.liberos) " L" else ""
        val name = (player?.shortName ?: player?.name ?: "").trim().take(10)
        val sb = SpannableStringBuilder()
        val posStart = sb.length
        sb.append("P$pos\n")
        sb.setSpan(dimSpan(), posStart, sb.length, SPAN_FLAGS)
        val numStart = sb.length
        sb.append(big)
        sb.setSpan(RelativeSizeSpan(2.0f), numStart, sb.length, SPAN_FLAGS)
        sb.setSpan(StyleSpan(Typeface.BOLD), numStart, sb.length, SPAN_FLAGS)
        sb.append(suffix)
        val nameStart = sb.length
        // A space, not nothing, on the third line of an empty slot:
        // an empty trailing line is not measured, and the button would
        // shrink.
        sb.append("\n${name.ifBlank { " " }}")
        sb.setSpan(dimSpan(), nameStart, sb.length, SPAN_FLAGS)
        return sb
    }

    /** Caption grey for the lines flanking a jersey number. A fresh
     *  instance per range: one span object cannot cover two. */
    private fun dimSpan() = ForegroundColorSpan(Color.parseColor("#BFBFBF"))

    private fun spacer(px: Int) = View(this).apply {
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, px,
        )
    }

    private fun parseColor(hex: String?, fallback: String): Int =
        runCatching { Color.parseColor(hex ?: fallback) }.getOrElse { Color.parseColor(fallback) }

    // ---- scoring taps (with confirmation) ---------------------------

    /**
     * `+1` for [side]. If the last score has not yet been followed by
     * a `ball_served` event, prompt for confirmation -- almost always
     * the operator hit `+1` before the rally actually started.
     */
    private fun onPlusOne(side: Side) {
        beginAction()
        val need = !gameState.ballServedSinceLastScore
        if (need) {
            AlertDialog.Builder(this)
                .setTitle("Add point without ball served?")
                .setMessage(
                    "No 'ball served' since the last score. " +
                    "Tap Confirm if the rally really happened and " +
                    "you missed the ball-served tap."
                )
                .setPositiveButton("Confirm") { _, _ -> doScore(side) }
                .setNegativeButton("Cancel", null)
                .show()
        } else {
            doScore(side)
        }
    }

    private fun doScore(side: Side) {
        record(EventType.Score, mapOf("team" to side.wire))
    }

    /**
     * `-1` for [side]. Always confirms: a subtraction is either a
     * correction of a previous mis-tap or a mis-tap itself, and the
     * cost of confirming is one tap.
     */
    private fun onMinusOne(side: Side) {
        AlertDialog.Builder(this)
            .setTitle("Subtract a point?")
            .setMessage(
                "This records a score correction of -1 for " +
                "${if (side == Side.Home) "us" else "them"}. Continue?"
            )
            .setPositiveButton("Confirm") { _, _ -> doCorrection(side, -1) }
            .setNegativeButton("Cancel", null)
            .show()
    }

    /** End of set: the rotation clears and the scores reset, so the
     *  message names who takes it, at what score. */
    private fun confirmSetEnd() {
        val s = gameState
        val takes = when {
            s.homeScore > s.awayScore -> teamName(Side.Home)
            s.awayScore > s.homeScore -> teamName(Side.Away)
            else -> null
        }
        confirm(
            "End the set?",
            (if (takes != null) "$takes takes it ${s.homeScore}-${s.awayScore}."
             else "Scores are level at ${s.homeScore}-${s.awayScore}, " +
                  "so neither side is credited with the set.") +
            "\n\nScores reset and the rotation clears.",
            "End set",
        ) { record(EventType.SetEnd) }
    }

    /**
     * End of match: close the set being played, stop scoring, raise
     * the FINAL card.
     *
     * A match ends on the last point of the last set, so Game End
     * closes that set too -- there is no such thing as a finished
     * match with an unfinished set, and warning about one was
     * describing a state the operator could not do anything useful
     * about. It also kept the deciding set off the FINAL card, which
     * is what made that card unreadable.
     *
     * Recorded as a real `set_end` event ahead of the `game_end`,
     * not folded into the engine's handling of `game_end`. VME
     * replays this log, and a phone that quietly meant "and also end
     * the set" would render a different match on import. The cost is
     * that Undo takes two taps here, which is the honest price of
     * two things having happened.
     */
    private fun confirmGameEnd() {
        val s = gameState
        val ending = s.homeScore != 0 || s.awayScore != 0
        val played = (s.setScores + (if (ending) listOf(s.homeScore to s.awayScore) else emptyList()))
            .takeIf { it.isNotEmpty() }
            ?.joinToString("   ") { (h, a) -> "$h-$a" }
        confirm(
            "End the match?",
            (if (played != null) "Sets: $played\n\n" else "") +
            "This raises the FINAL card and stops scoring.",
            "End match",
        ) {
            if (ending) record(EventType.SetEnd)
            record(EventType.GameEnd)
        }
    }

    private fun doCorrection(side: Side, delta: Int) {
        val payload = if (side == Side.Home) mapOf("home_delta" to delta, "away_delta" to 0)
                      else mapOf("home_delta" to 0, "away_delta" to delta)
        record(EventType.ScoreCorrection, payload)
    }

    // ---- event recording --------------------------------------------

    /**
     * Append one event, persist immediately, refresh every view that
     * shows game state. The on-disk copy is the source of truth even
     * after a crash.
     */
    /**
     * When the operator started the action now being recorded, or null
     * when the tap that started it is the tap that finished it.
     *
     * An event's moment is the moment of the FIRST tap. Tagging a kill
     * is "Kill", then find the player, then tap their slot - two to
     * four seconds during which the match has moved on, and the event
     * used to be stamped at the end of it. Everything downstream is
     * placed by that timestamp: the frame it lands on in VME, the reel
     * it gets cut into, the pop-up's position in the broadcast. Half a
     * rally late is the difference between a highlight that opens on
     * the hit and one that opens on the celebration.
     */
    private var actionStartedAt: Long? = null

    /** Start the clock for a multi-tap action. */
    private fun beginAction() {
        actionStartedAt = System.currentTimeMillis()
    }

    private fun record(type: EventType, payload: Map<String, Any?> = emptyMap()) {
        val started = actionStartedAt
        actionStartedAt = null
        val now = System.currentTimeMillis()
        // Backdated to the first tap, but only so far. A start that old
        // is not a slow selection, it is an armed action the operator
        // walked away from and came back to - and stamping the event
        // minutes early would be worse than stamping it late.
        val at = when {
            started == null -> now
            now - started > MAX_ACTION_BACKDATE_MS -> {
                log("event: ignoring a stale start (${(now - started) / 1000}s)")
                now
            }
            else -> started
        }
        // Play resuming ends a timeout, however the operator left it.
        // While one is on, the only other live control is Ball Served,
        // and VME cuts from a timeout's start to its END - so the end
        // must be in the log, not just the card coming down. Logged
        // first, at the same instant, so it sorts ahead of the serve.
        if (timeoutShown && type != EventType.TimeoutEnd) {
            append(EventType.TimeoutEnd, emptyMap(), at)
            log("event: ${EventType.TimeoutEnd.wire} (play resumed)")
        }
        // Snapshot BEFORE the event lands. A substitution pop-up has
        // to name the player leaving the court, and by the time the
        // event is in the list that slot already holds the incoming
        // player -- which is why subs were reading "#7 -> #7".
        val before = gameState
        append(type, payload, at)
        store.save(match)

        refreshAllViews()
        pushOverlay()
        firePopup(type, payload, before)
        log("event: ${type.wire}${if (payload.isNotEmpty()) " $payload" else ""}")
        enforceLiberoRule(type)
    }

    /** Add one event to the in-memory match. The caller saves. */
    private fun append(type: EventType, payload: Map<String, Any?>, at: Long) {
        val nextId = (match.events.maxOfOrNull { it.id } ?: 0L) + 1L
        match = match.copy(
            events = match.events + MatchEvent(
                id = nextId, type = type, payload = payload, at = at,
            ),
        )
    }

    // ---- libero rotation rule ---------------------------------------

    /**
     * A libero may only play the back row. When a side-out rotates
     * one into P4, the player they came on for goes back in for them
     * -- automatically, because the operator is watching the court,
     * not the phone, and this swap comes round every six rotations.
     *
     * Only scoring events rotate, so only they can trigger this. The
     * swap is an ordinary substitution event: VME replays it like
     * any other and Undo removes it like any other. When nobody is
     * remembered (the libero was placed into an empty slot at set
     * start, or their partner is somehow already on court) the
     * picker opens instead, with liberos filtered out -- the one
     * thing that cannot go into a front-row slot is another libero.
     */
    private fun enforceLiberoRule(after: EventType) {
        val scoring = after == EventType.Score ||
            after == EventType.Kill ||
            after == EventType.Ace
        // A prompt held back from a Kill (see below) fires on the next
        // event, whatever it turns out to be.
        if (!scoring && !liberoPromptDeferred) return
        val s = gameState
        val hit = GameStateEngine.frontRowLibero(s, liberos)
        if (hit == null) {
            liberoPromptDeferred = false
            return
        }
        val (pos, libero) = hit
        val sub = { jersey: Int ->
            record(EventType.Substitution, mapOf(
                "team" to Side.Home.wire,
                "position" to pos,
                "player_in_number" to jersey,
            ))
        }
        // This set's pairing first, then whoever this libero last came
        // on for earlier in the match. The fallback is what stops the
        // first rotation of every set asking a question the set before
        // already answered.
        val back = (s.liberoReplacements[libero] ?: s.liberoLastPartners[libero])
            ?.takeIf { it !in liberos && it !in s.homePositions.values }
        if (back != null) {
            liberoPromptDeferred = false
            log("libero: #$libero rotated to P$pos, #$back returns")
            sub(back)
            return
        }
        // Nobody to put back automatically, so this has to be asked -
        // but a Kill is normally followed straight away by the Assist,
        // and a dialog over the grid eats that tap. Hold it for one
        // event. Nothing is lost if the operator does something else:
        // the rule re-reads live state every time it runs.
        if (after == EventType.Kill && !liberoPromptDeferred) {
            liberoPromptDeferred = true
            log("libero: #$libero rotated to P$pos - asking after the assist")
            return
        }
        liberoPromptDeferred = false
        log("libero: #$libero rotated to P$pos - pick who comes back")
        pickPlayer(Side.Home, excludeOnCourt = true, excludeLiberos = true, onPicked = sub)
    }

    /** A libero prompt waiting for the Assist that follows a Kill. */
    private var liberoPromptDeferred = false

    /**
     * Push the current state to the overlay, but never let an
     * overlay error crash the scoring surface -- the event is
     * already on disk, the operator can keep tagging even if the
     * scoreboard freezes on-screen. Any exception is logged so it
     * shows up in the on-device log.
     */
    private fun pushOverlay() {
        val ov = overlay ?: return
        runCatching {
            // VME's GameStart / GameEnd carry a
            // ScoreboardVisibilityEffect, so the bar only exists
            // between the whistle and the final point -- warm-up and
            // post-match footage stay clean.
            if (gameState.gameStarted && !gameState.gameEnded) {
                ov.update(match, gameState)
            } else {
                ov.hide()
            }
        }.onFailure { log("overlay update failed: ${it.javaClass.simpleName} ${it.message}") }

        // The centre card owns every "not in play" state, and they
        // are mutually exclusive: before the whistle it advertises
        // the fixture, during a timeout it holds the score, after
        // the final point it shows the result. Any of them replaces
        // the scoreboard rather than sitting alongside it.
        runCatching {
            val c = card ?: return@runCatching
            when {
                gameState.gameEnded -> c.showFinal(gameState)
                !gameState.gameStarted -> c.showUpcoming()
                timeoutShown -> c.showTimeout(gameState)
                setEndShown -> c.showSetEnd(gameState)
                else -> c.hide()
            }
        }.onFailure { log("card failed: ${it.javaClass.simpleName} ${it.message}") }

        pushCourt()
    }

    /**
     * Hand the current line-up to the court diagram.
     *
     * Called on every push even when the diagram is hidden: switching
     * to court-only mode mid-match has to find it already drawn, and
     * the overlay skips the redraw itself when nothing moved.
     */
    private fun pushCourt() {
        val co = court ?: return
        val s = gameState
        val slots = (1..6).map { pos ->
            val jersey = s.homePositions[pos]
            val player = jersey?.let { j ->
                match.homeRoster.players.firstOrNull { it.number == j }
            }
            CourtOverlay.Slot(
                position = pos,
                jersey = jersey,
                name = (player?.shortName ?: player?.name ?: "").trim().take(14),
                photo = courtPhoto(player?.localPhotoPath),
                libero = jersey != null && jersey in match.liberos,
            )
        }
        // The server stands in position 1, so the ring goes there --
        // and only when it is our serve.
        val serving = if (s.servingTeam == Side.Home) 1 else null
        runCatching { co.update(slots, serving) }
            .onFailure { log("court failed: ${it.javaClass.simpleName} ${it.message}") }
    }

    /** Photos for the court diagram, decoded once each. Bigger than
     *  the pop-up avatars because the diagram draws them several times
     *  that size. */
    private val courtPhotos = mutableMapOf<String, android.graphics.Bitmap?>()

    private fun courtPhoto(path: String?): android.graphics.Bitmap? {
        val key = path.orEmpty()
        if (key.isBlank()) return null
        return courtPhotos.getOrPut(key) { PhotoCache.load(key, COURT_PHOTO_PX) }
    }

    /**
     * Fire a pop-up on the video for this event, matching what VME's
     * renderer would draw for the same event in a finished match.
     *
     * Wrapped in `runCatching` for the same reason as [pushOverlay]:
     * the event is already persisted, an overlay failure must never
     * take the scoring surface down.
     */
    private fun firePopup(
        type: EventType,
        payload: Map<String, Any?>,
        before: GameState,
    ) {
        val p = popup
        if (p == null) {
            // Not an error -- pop-ups only exist while streaming.
            // Logged anyway so "no pop-up appeared" can be traced to
            // "we never created one" vs "we drew one and the GL
            // pipeline swallowed it".
            log("popup: skipped for ${type.wire} (not streaming)")
            return
        }
        // In the no-video modes the point announcement replaces the
        // play's own pop-up, rather than queueing behind it: a second
        // `show` cuts the first one's slide short, and one card that
        // says who scored, what it was and where the score now stands
        // beats half of two.
        val spec = pointSpec(type, payload) ?: popupSpec(type, payload, before)
        if (spec == null) {
            log("popup: ${type.wire} is silent (matches VME)")
            return
        }
        runCatching {
            p.show(
                title = spec.title,
                subtitle = spec.subtitle,
                teamColor = spec.color,
                // Longer with no video: the pop-up is the only thing
                // on screen that moves, so it is worth reading rather
                // than glancing at, and nothing is hidden behind it.
                holdMs = if (quality.video) 3000L else 5000L,
                avatars = spec.avatars,
            )
        }
            .onSuccess { log("popup: ${spec.title} / ${spec.subtitle ?: ""}") }
            .onFailure { log("popup failed: ${it.javaClass.simpleName} ${it.message}") }
    }

    /**
     * "Point Eastlake" -- the every-point announcement the no-video
     * modes need, or null when the picture is live.
     *
     * With the camera blacked out or replaced by the court diagram,
     * nothing on screen moves when a rally ends: the scoreboard ticks
     * over, and a viewer who looked away has no idea a point happened
     * at all, let alone who won it. On live video the play tells them
     * and the existing pop-ups annotate it, which is why this stays
     * out of the way there.
     *
     * The score goes in the subtitle and the scorer's face, when the
     * event names one, in the avatar -- so the card carries the same
     * three facts the missing picture would have.
     */
    private fun pointSpec(type: EventType, payload: Map<String, Any?>): PopupSpec? {
        if (quality.video) return null
        val play = when (type) {
            EventType.Score -> null
            EventType.Kill -> "Kill"
            EventType.Ace -> "Ace"
            else -> return null
        }
        val side = Side.fromWire(payload["team"] as? String) ?: return null
        val s = gameState
        val score = "${s.homeScore} - ${s.awayScore}"
        val jersey = (payload["player_number"] as? Number)?.toInt()
        val roster = if (side == Side.Away) match.opponentRoster else match.homeRoster
        val photo = roster.players.firstOrNull { it.number == jersey }?.localPhotoPath
        return PopupSpec(
            // Capped: the pop-up's canvas is measured once, against a
            // two-player substitution subtitle, and a club that spells
            // its full name out would be clipped mid-word instead.
            title = "Point ${teamName(side).take(20)}",
            subtitle = if (play == null) score else "$play  ·  $score",
            color = when (side) {
                Side.Home -> parseColor(match.homeRoster.teamColor, DEFAULT_HOME_COLOR)
                Side.Away -> parseColor(match.opponentRoster.teamColor, DEFAULT_AWAY_COLOR)
            },
            // Badge first, then the scorer: the crest belongs to the
            // title above it ("Point Eastlake"), the face to the play
            // in the subtitle, and left-to-right is the order they are
            // read in. The opponent has no roster, so on their points
            // the crest is all there is -- which is exactly when it
            // matters most, because the name alone is just text.
            avatars = listOfNotNull(
                logoPath(roster)?.let {
                    PopupOverlay.Avatar(jersey = null, photoPath = it, isLogo = true)
                },
                jersey?.let { PopupOverlay.Avatar(jersey = it, photoPath = photo) },
            ),
        )
    }

    /** A roster's cached badge path, or null when there is no file to
     *  draw -- an empty disc says less than no disc at all. */
    private fun logoPath(roster: Roster): String? {
        val path = roster.localLogoPath
        if (path.isNullOrBlank()) return null
        return if (java.io.File(path).exists()) path else null
    }

    /** Title + subtitle + tint + avatars for one pop-up. */
    private data class PopupSpec(
        val title: String,
        val subtitle: String?,
        val color: Int,
        val avatars: List<PopupOverlay.Avatar> = emptyList(),
    )

    /**
     * Pop-up content for [type], or null when VME renders nothing.
     *
     * This is a direct port of the `overlay_effect` properties on
     * the event classes under `backend/app/domain/events/`
     * (player.py, serve.py, roster.py, scoring.py), so the live
     * stream and the rendered match show the same pop-ups.
     *
     * NOTE: do not write a glob like `events/` followed by a star
     * and `.py` in this KDoc -- the slash-star opens a nested block
     * comment and every declaration below it silently vanishes from
     * the class.
     *
     *
     * | event             | title    | subtitle                 |
     * |-------------------|----------|--------------------------|
     * | `kill`            | Kill     | `#NN Name`               |
     * | `ace`             | Ace      | `#NN Name`               |
     * | `assist`          | Assist   | `#NN Name`               |
     * | `block`           | Block    | `#NN Name`               |
     * | `dive`            | Dive     | `#NN Name`               |
     * | `dig`             | Dig      | `#NN Name`               |
     * | `ball_served`     | Serving  | server at position 1     |
     * | `substitution`    | Sub      | `#OUT Name -> #IN Name`  |
     * | `score_correction`| Score Fix| deltas                   |
     *
     * Silent in VME, therefore silent here:
     *
     * - `highlight` -- `HighlightEvent.overlay_effect` explicitly
     *   returns None. It exists to mark a frame for the `highlights/`
     *   batch render, not to add noise to the full render.
     * - `first_serve` -- carries only a `roster_effect`
     *   (`ServeTeamSetEffect`). The serve itself pops via
     *   `ball_served`, which is the event that knows who is actually
     *   standing at position 1.
     * - `game_start` / `game_end` -- `ScoreboardVisibilityEffect`
     *   only; they show and hide the scoreboard.
     * - `set_end` -- score reset, no pop-up.
     * - `score` -- no `overlay_effect`; the scoreboard numbers change
     *   in the same frame.
     * - `replay`, `cut_start`, `cut_end`, `clip_transition`,
     *   `focus_in`, `focus_out` -- editor metadata.
     */
    /**
     * @param before game state as it was *before* this event was
     *   applied. Substitution needs it to name the outgoing player;
     *   everything else is unaffected either way, but taking the
     *   pre-event state uniformly matches VME, whose
     *   `overlay_effect_for_state` is handed the same thing.
     */
    private fun popupSpec(
        type: EventType,
        payload: Map<String, Any?>,
        before: GameState,
    ): PopupSpec? {
        val side = Side.fromWire(payload["team"] as? String)
        val jersey = (payload["player_number"] as? Number)?.toInt()

        fun tint(s: Side?): Int = when (s) {
            Side.Home -> parseColor(match.homeRoster.teamColor, DEFAULT_HOME_COLOR)
            Side.Away -> parseColor(match.opponentRoster.teamColor, DEFAULT_AWAY_COLOR)
            null -> PopupOverlay.DEFAULT_BG
        }

        fun rosterFor(s: Side?) =
            if (s == Side.Away) match.opponentRoster else match.homeRoster

        fun player(s: Side?, n: Int?): String? {
            if (n == null) return null
            val name = rosterFor(s).players.firstOrNull { it.number == n }
                ?.let { it.shortName ?: it.name }
            return if (name.isNullOrBlank()) "#$n" else "#$n $name"
        }

        /** Avatar for a jersey: the cached photo when the roster has
         *  one, otherwise a jersey-number disc (PopupOverlay decides
         *  which, from whether photoPath is null). */
        fun avatar(s: Side?, n: Int?): PopupOverlay.Avatar? {
            if (n == null) return null
            val photo = rosterFor(s).players
                .firstOrNull { it.number == n }?.localPhotoPath
            return PopupOverlay.Avatar(jersey = n, photoPath = photo)
        }

        /** VME's PlayerEvent popup: action label + player subtitle. */
        fun playerPopup(label: String) = PopupSpec(
            label, player(side, jersey), tint(side),
            listOfNotNull(avatar(side, jersey)),
        )

        return when (type) {
            EventType.Kill   -> playerPopup("Kill")
            EventType.Ace    -> playerPopup("Ace")
            EventType.Assist -> playerPopup("Assist")
            EventType.Block  -> playerPopup("Block")
            EventType.Dive   -> playerPopup("Dive")
            EventType.Dig    -> playerPopup("Dig")

            // BallServedEvent.overlay_effect_for_state: the server is
            // whoever stands at position 1 of the serving team, so it
            // comes from game state rather than the event payload.
            // VME returns None when the server cannot be identified
            // ("Serving #None" would be worse than no pop-up) -- we
            // still pop, but without a subtitle, because courtside the
            // operator wants confirmation the tap registered.
            // Only our own serve pops. We track the home rotation,
            // so we can name the server; the opponent's lineup is not
            // tracked (VME does not either), which would leave a bare
            // "Serving" with no name -- noise on every one of their
            // rallies for no information.
            EventType.BallServed -> {
                val serving = before.servingTeam
                if (serving != Side.Home) return null
                val server = before.homePositions[1]
                PopupSpec(
                    "Serving", player(Side.Home, server), tint(Side.Home),
                    listOfNotNull(avatar(Side.Home, server)),
                )
            }

            // SubstitutionEvent.overlay_effect: "#OUT name -> #IN name".
            // Outgoing on the left, incoming on the right, matching the
            // reading order of "who became who".
            EventType.Substitution -> {
                val inNum = (payload["player_in_number"] as? Number)?.toInt()
                val pos = (payload["position"] as? Number)?.toInt()
                val outNum = pos?.let { before.homePositions[it] }
                val inLabel = player(side, inNum)
                val outLabel = player(side, outNum)
                // A slot that was empty has nobody to swap out, so
                // "#7 for #7" would be nonsense. Treat it as a player
                // taking the court rather than a substitution.
                val isEntering = outNum == null
                val sub = if (isEntering) inLabel
                          else "$outLabel  →  $inLabel"
                // Two circles, ordered out -> in to match the
                // subtitle. VME draws both even when only one has a
                // photo: one lone face beside a two-player subtitle
                // is what makes it ambiguous.
                PopupSpec(
                    if (isEntering) "Entering" else "Sub",
                    sub, tint(side),
                    listOfNotNull(avatar(side, outNum), avatar(side, inNum)),
                )
            }

            // ScoreCorrectionEvent.overlay_effect: a MessagePopup with
            // the non-zero deltas, default tint (not a team colour).
            EventType.ScoreCorrection -> {
                val hd = (payload["home_delta"] as? Number)?.toInt() ?: 0
                val ad = (payload["away_delta"] as? Number)?.toInt() ?: 0
                val parts = buildList {
                    if (hd != 0) add("%s %+d".format(teamName(Side.Home), hd))
                    if (ad != 0) add("%s %+d".format(teamName(Side.Away), ad))
                }
                PopupSpec(
                    "Score Fix",
                    if (parts.isEmpty()) "±0" else parts.joinToString("  ·  "),
                    PopupOverlay.DEFAULT_BG,
                )
            }

            else -> null
        }
    }

    /** Display name for a side, falling back to the match-level
     *  team/opponent string and finally to HOME / AWAY. */
    private fun teamName(side: Side?): String = when (side) {
        Side.Home -> match.homeRoster.teamName.ifBlank { match.team }
            .ifBlank { "HOME" }.uppercase()
        Side.Away -> match.opponentRoster.teamName.ifBlank { match.opponent }
            .ifBlank { "AWAY" }.uppercase()
        null -> ""
    }

    /** Repaint every view that depends on `gameState` or `match`. */
    private fun refreshAllViews() {
        val s = gameState
        scoreHomeBig.text = s.homeScore.toString()
        scoreAwayBig.text = s.awayScore.toString()
        homeServeDot.visibility =
            if (s.servingTeam == Side.Home) View.VISIBLE else View.INVISIBLE
        awayServeDot.visibility =
            if (s.servingTeam == Side.Away) View.VISIBLE else View.INVISIBLE
        // Position grid: show jersey number and the player's short
        // name if we have one, in a smaller font under the jersey.
        // Empty slot renders `P#` on top and `?` underneath so the
        // grid does not visibly change height with substitutions.
        for ((pos, btn) in positionButtons) {
            val jersey = s.homePositions[pos]
            btn.text = positionLabel(pos, jersey)
        }
        // Ball Served greys out after a tap so the operator has a
        // visible signal that the rally is marked; it comes back live
        // as soon as the next Score/Replay clears the flag.
        val served = s.ballServedSinceLastScore
        ballServedButton.isEnabled = !served
        ballServedButton.alpha = if (served) 0.4f else 1.0f
        ballServedButton.text =
            if (served) "$LBL_BALL_SERVED \u2713" else LBL_BALL_SERVED
        timeoutButton.text = if (timeoutShown) LBL_TIMEOUT_END else LBL_TIMEOUT
        // Undo is disabled when there is nothing to roll back --
        // otherwise the button implies an action it cannot take.
        val hasEvents = match.events.isNotEmpty()
        undoButton.isEnabled = hasEvents
        undoButton.alpha = if (hasEvents) 1.0f else 0.4f

        // Nothing is taggable before the first whistle or after the
        // last point: a Kill with no game around it is not a real
        // event, and leaving the buttons live invites mis-taps during
        // warm-up. Game Start is the inverse -- live only while the
        // game is not running.
        val live = s.gameStarted && !s.gameEnded
        for (b in gatedButtons) {
            b.isEnabled = live
            b.alpha = if (live) 1.0f else 0.4f
        }
        // Ball Served has its own within-rally rule on top of the gate.
        if (live && served) {
            ballServedButton.isEnabled = false
            ballServedButton.alpha = 0.4f
        }
        // Nothing can happen in a rally that has not started. A Kill,
        // Dig or Block tagged between points is either a mis-tap or an
        // event from the last rally arriving after the point was
        // recorded, and both land on the wrong side of the score.
        //
        // Scoring is deliberately still open: +1 with no serve behind
        // it is how the operator corrects a miscount, and the engine
        // treats it as exactly that (see GameStateEngine.onScore).
        if (!served) {
            for (b in actionButtons) {
                b.isEnabled = false
                b.alpha = 0.4f
            }
        }

        // A timeout freezes the surface. Play has stopped, so nothing
        // is taggable, and the only taps that mean anything are the
        // two that resume: Ball Served (the next rally has begun) and
        // Timeout itself (the operator is ending it). Both log the
        // timeout's end -- Timeout directly, Ball Served through the
        // close in `record` -- and the card follows the log.
        //
        // Deliberately overrides the within-rally rule above: if a
        // timeout was called after a serve, Ball Served must stay
        // reachable, because it is half of the way out of here.
        if (timeoutShown) {
            // The rotation grid stays live: a timeout is when
            // substitutions actually happen, and with the grid frozen
            // the operator had to end the timeout, sub, and call it
            // again - three taps to record one thing that happened
            // while the card was up. Nothing is armed during a
            // timeout, so a slot tap means "substitute here".
            val slots = positionButtons.values
            for (b in gatedButtons) {
                val usable =
                    b === ballServedButton || b === timeoutButton || b in slots
                b.isEnabled = usable && live
                b.alpha = if (usable && live) 1.0f else 0.4f
            }
        }
        // Liberos is a setup control, not a gated one, so the freeze
        // has to reach it by hand.
        liberoBtn.isEnabled = !timeoutShown
        liberoBtn.alpha = if (timeoutShown) 0.4f else 1.0f

        // Armed mode: the six grid slots are the only live controls,
        // and they are lit so the one legal next tap is obvious.
        // Everything else goes dark -- including the action buttons,
        // so a double-tap on "Kill" cannot queue a second action
        // (the button itself stays enabled purely so it can disarm).
        val armed = armedAction
        // The Kill button becomes the Assist button for the follow-up
        // tap: the prompt is a second stage of the same action, and
        // showing it on the button that started it is what makes the
        // grid's highlight legible ("Assist -> tap the setter") instead
        // of a lit Kill button that has already been recorded.
        killButton.text = if (armed == EventType.Assist) LBL_ASSIST else LBL_KILL
        cancelActionButton.visibility = if (armed != null) View.VISIBLE else View.GONE
        cancelActionButton.text =
            if (armed == EventType.Assist) LBL_NO_ASSIST else LBL_CANCEL
        if (armed != null) {
            for (b in gatedButtons) {
                b.isEnabled = false
                b.alpha = 0.25f
            }
            for ((pos, btn) in positionButtons) {
                val filled = s.homePositions[pos] != null
                btn.isEnabled = true
                btn.alpha = if (filled) 1.0f else 0.35f
                tintButton(btn, if (filled) ARMED_SLOT_BG else ARMED_SLOT_EMPTY_BG)
            }
            for (b in actionButtons) {
                if (b.text == armedLabel) {
                    b.isEnabled = true
                    b.alpha = 1.0f
                    tintButton(b, ARMED_SLOT_BG)
                }
            }
        } else {
            for (btn in positionButtons.values) restoreButton(btn)
            for (b in actionButtons) restoreButton(b)
        }
        gameStartButton.isEnabled = !live && !timeoutShown
        gameStartButton.alpha = if (!live && !timeoutShown) 1.0f else 0.4f
        refreshTeamChrome()
    }

    /**
     * Roll back the last event. Recomputing state from the trimmed
     * event list is cheaper than trying to invert individual events,
     * and guarantees that Undo followed by no further edits leaves
     * the state byte-identical to "before the last tap".
     */
    private fun onUndo() {
        val last = match.events.lastOrNull() ?: return
        match = match.copy(events = match.events.dropLast(1))
        store.save(match)
        refreshAllViews()
        pushOverlay()
        log("undo: dropped ${last.type.wire} #${last.id}")
    }

    // ---- dialogs ----------------------------------------------------

    /**
     * Show a jersey picker for [side] and hand the chosen number
     * to [onPicked]. When [excludeOnCourt] is true (substitution
     * flow), players already in the home rotation are filtered
     * out -- there is no point subbing someone in who is already
     * on the court, and showing them was a real source of
     * courtside mis-taps. [excludeLiberos] additionally hides the
     * liberos, for a slot they are not allowed to fill.
     */
    private fun pickPlayer(
        side: Side,
        excludeOnCourt: Boolean = false,
        excludeLiberos: Boolean = false,
        onPicked: (Int) -> Unit,
    ) {
        val roster = if (side == Side.Home) match.homeRoster else match.opponentRoster
        val onCourt: Set<Int> =
            if (excludeOnCourt && side == Side.Home)
                gameState.homePositions.values.filterNotNull().toSet()
            else emptySet()
        val available = roster.players
            .sortedBy { it.number }
            .filter { it.number !in onCourt }
            .filter { !(excludeLiberos && it.number in liberos) }
        // Rows are built by hand rather than handed to `setItems`
        // so each one can carry the player's face. `setItems` takes
        // strings and nothing else, which is why the photos fetched
        // at import only ever reached the video pop-ups. At courtside
        // the operator knows the squad by sight well before they know
        // the numbers, and picking off a list of bare jerseys was the
        // slowest step in a substitution.
        val adapter = object : android.widget.BaseAdapter() {
            override fun getCount() = available.size + 1
            override fun getItem(i: Int): Any? = available.getOrNull(i)
            override fun getItemId(i: Int) = i.toLong()
            override fun getView(i: Int, convert: View?, parent: ViewGroup): View =
                // Deliberately not recycling `convert`: a roster is a
                // dozen rows on screen at once, so there is nothing to
                // recycle, and reusing a row would mean clearing a
                // stale bitmap on every bind.
                if (i == available.size) addJerseyRow() else playerRow(available[i], side)
        }
        AlertDialog.Builder(this)
            .setTitle("Player - ${roster.teamName.ifBlank { side.wire }}")
            .setAdapter(adapter) { _, which ->
                if (which == available.size) addJerseyDialog(side) { onPicked(it) }
                else onPicked(available[which].number)
            }
            .show()
    }

    /**
     * Gap between adjacent controls, in pixels.
     *
     * Round 15 gave the buttons rounded, coloured fills, and without
     * a gap they butt together into one slab -- the corners read as
     * notches cut out of a single bar rather than as separate
     * buttons. Density-scaled, because a fixed pixel gap is a hair
     * on this phone and a gutter on a tablet.
     */
    private val gapPx get() = (6 * resources.displayMetrics.density).toInt()

    /** Height and width of a picker avatar, in pixels. */
    private val avatarPx get() = (40 * resources.displayMetrics.density).toInt()

    /** One picker row: face, then jersey and name. */
    private fun playerRow(p: Player, side: Side): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(48, 16, 48, 16)
        }
        row.addView(
            avatarView(p, side),
            LinearLayout.LayoutParams(avatarPx, avatarPx).apply { marginEnd = 28 },
        )
        // The (L) tag is a home-team fact; an opponent wearing the
        // same number as our libero must not inherit it.
        val tag = if (side == Side.Home) match.jerseyLabel(p.number) else "#${p.number}"
        row.addView(TextView(this).apply {
            text = "$tag  ${p.shortName ?: p.name}"
            setTextColor(Color.WHITE)
            textSize = 16f
            isSingleLine = true
            ellipsize = android.text.TextUtils.TruncateAt.END
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        return row
    }

    /**
     * The player's cached photo cropped to a circle, or a disc
     * carrying their number when there is none.
     *
     * Decoded at the size actually drawn, for the same reason the
     * pop-up renderer does it -- a roster of twelve multi-megapixel
     * photos decoded at full size would be the one place this list
     * could run the phone out of memory.
     */
    private fun avatarView(p: Player, side: Side): View {
        val bmp = PhotoCache.load(p.localPhotoPath, avatarPx)
        if (bmp != null) {
            return android.widget.ImageView(this).apply {
                setImageDrawable(
                    androidx.core.graphics.drawable.RoundedBitmapDrawableFactory
                        .create(resources, bmp)
                        .apply { isCircular = true },
                )
            }
        }
        // No photo: the team-coloured disc the pop-ups fall back to,
        // so the two surfaces degrade the same way.
        val fill = if (side == Side.Home)
            parseColor(match.homeRoster.teamColor, DEFAULT_HOME_COLOR)
        else parseColor(match.opponentRoster.teamColor, DEFAULT_AWAY_COLOR)
        return TextView(this).apply {
            text = p.number.toString()
            setTextColor(Color.WHITE)
            textSize = 14f
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
            background = android.graphics.drawable.GradientDrawable().apply {
                shape = android.graphics.drawable.GradientDrawable.OVAL
                setColor(fill)
            }
        }
    }

    /** Last row of the picker: the escape hatch for a jersey that is
     *  not on the roster. Green, like the section headers, so it
     *  reads as a command rather than a twelfth player. */
    private fun addJerseyRow(): View = TextView(this).apply {
        text = "+ Add jersey..."
        setTextColor(Color.parseColor("#8FE38F"))
        textSize = 16f
        setPadding(48, 28, 48, 28)
    }

    private fun addJerseyDialog(side: Side, onAdded: (Int) -> Unit) {
        val input = EditText(this).apply {
            hint = "jersey #"
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
        }
        AlertDialog.Builder(this)
            .setTitle("Add jersey")
            .setView(input)
            .setPositiveButton("Add") { _, _ ->
                val n = input.text.toString().toIntOrNull() ?: return@setPositiveButton
                addPlayer(side, n)
                onAdded(n)
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun addPlayer(side: Side, jersey: Int) {
        val roster = if (side == Side.Home) match.homeRoster else match.opponentRoster
        if (roster.players.any { it.number == jersey }) return
        val added = roster.copy(players = roster.players + Player(name = "#$jersey", number = jersey))
        match = if (side == Side.Home) match.copy(homeRoster = added)
                else match.copy(opponentRoster = added)
        store.save(match)
    }

    /** Substitute directly into a specific position (from tapping a
     *  grid slot). Skips the position-picker step. Players already
     *  on the court are hidden from the picker -- see [pickPlayer]. */
    private fun subForPosition(pos: Int) {
        beginAction()
        pickPlayer(Side.Home, excludeOnCourt = true) { jersey ->
            record(EventType.Substitution, mapOf(
                "team" to Side.Home.wire,
                "position" to pos,
                "player_in_number" to jersey,
            ))
        }
    }

    // ---- streaming --------------------------------------------------

    /**
     * One press: permission -> prepare -> preview -> token -> broadcast -> stream.
     *
     * Every step logs both to logcat and to the on-screen status
     * label so the operator can see where a failure happened without
     * a laptop. `statusLabel` shows the current phase; `logView`
     * keeps the audit trail.
     */
    private fun onStartStreaming() {
        if (stream.isStreaming) { log("already streaming"); return }
        if (!ensurePrepared()) return
        routeAudioToUsbIfPresent()
        stream.getStreamClient().setReTries(10)

        // Blackout goes on FIRST: filters composite in the order they
        // were added, so everything below draws over it and the
        // scoreboard survives the camera being painted out.
        val bo = BlackoutOverlay()
        blackout = bo
        stream.getGlInterface().addFilter(bo.filter)
        bo.attach()
        bo.setShown(!quality.video)

        // The court diagram sits on the blackout and under everything
        // else, so in court-only mode the scoreboard and pop-ups still
        // land on top of it exactly as they do on live video.
        val co = CourtOverlay(
            videoWidth = target.width,
            videoHeight = target.height,
            teamColor = parseColor(match.homeRoster.teamColor, DEFAULT_HOME_COLOR),
        )
        court = co
        stream.getGlInterface().addFilter(co.filter)
        co.attach()
        co.setShown(quality.court || cameraLost)
        pushCourt()

        // Going live with the adapter already unplugged: the same
        // fallback as losing it mid-match, applied before the first
        // frame rather than after. Without this the UVC source would
        // sit there producing nothing and the broadcast would come up
        // and immediately stall.
        if (cameraLost) {
            log("camera: unplugged at Start - streaming the court view")
            runCatching { stream.changeVideoSource(blackVideoSource()) }
                .onFailure { log("camera: could not swap the video source: $it") }
            audioToPhoneMic()
        }

        // Attach the overlay. The filter is added once; per-event
        // updates only push a new bitmap via setImage().
        val ov = ScoreboardOverlay(
            videoWidth = target.width,
            videoHeight = target.height,
            homeName = match.homeRoster.teamName.ifBlank { match.team }.uppercase(),
            awayName = match.opponentRoster.teamName.ifBlank { match.opponent }.uppercase(),
            homeColor = parseColor(match.homeRoster.teamColor, DEFAULT_HOME_COLOR),
            awayColor = parseColor(match.opponentRoster.teamColor, DEFAULT_AWAY_COLOR),
            homeLogo = logoFor(Side.Home),
            awayLogo = logoFor(Side.Away),
        )
        overlay = ov
        stream.getGlInterface().addFilter(ov.filter)
        ov.attach()
        ov.update(match, gameState)

        // Popup overlay: attach the filter alongside the scoreboard
        // and toggle visibility via setAlpha (see PopupOverlay KDoc).
        // Attaching lazily on first show() did not work on the S24
        // -- addFilter after startStream silently dropped the popup.
        val pu = PopupOverlay(
            videoWidth = target.width,
            videoHeight = target.height,
            // Sit clear of the scoreboard bar rather than on top of it.
            scoreboardHeightPx = ov.barH,
        )
        popup = pu
        stream.getGlInterface().addFilter(pu.filter)
        pu.attach()
        // Diagnostic: fire an immediate popup on Start so the
        // operator sees whether the popup pipeline is working at
        // all -- if this does not show up on the phone screen or
        // in a preview player, the six player-tagged events are
        // never going to either, and the debugging shifts from
        // "my event handlers" to "the filter itself".
        // Pinned (holdMs = 0) so the operator can switch to the
        // Preview tab whenever and still see it. "Clear popup" in
        // the MATCH section takes it down.
        // Centre card: fixture / timeout / final score. Attached now,
        // its content chosen by state in pushOverlay.
        val fc = CardOverlay(
            videoWidth = target.width,
            videoHeight = target.height,
            homeName = teamName(Side.Home),
            awayName = teamName(Side.Away),
            homeColor = parseColor(match.homeRoster.teamColor, DEFAULT_HOME_COLOR),
            awayColor = parseColor(match.opponentRoster.teamColor, DEFAULT_AWAY_COLOR),
            homeLogo = logoFor(Side.Home),
            awayLogo = logoFor(Side.Away),
        )
        card = fc
        stream.getGlInterface().addFilter(fc.filter)
        fc.attach()

        // Bring the overlays in line with wherever the match already
        // is -- Start can happen mid-match after a stream drop, so
        // "game already started" and "game already ended" both have
        // to render correctly on the first frame.
        pushOverlay()

        wantPreview = true
        if (surfaceReady) attachPreview() else log("waiting for camera surface...")

        val token = accessToken
        if (token != null) {
            setStatus("contacting YouTube")
            goLiveWithToken(token)
        } else {
            setStatus("waiting for Google sign-in")
            YouTubeAuth.requestToken(this, consentLauncher, ::log) { fresh ->
                accessToken = fresh
                setStatus("contacting YouTube")
                goLiveWithToken(fresh)
            }
        }
    }

    /** Set the header status label from any thread. */
    private fun setStatus(text: String) = runOnUiThread {
        statusLabel.text = text
    }

    private fun attachPreview() {
        // Idempotent: both surfaceCreated (auto-preview) and
        // onStartStreaming call this; if the preview is already up
        // the second call is a no-op instead of a re-init.
        if (stream.isOnPreview) return
        log("camera: startPreview()")
        runCatching { stream.startPreview(surfaceView) }
            .onSuccess { log("camera: preview attached") }
            .onFailure {
                log("camera: startPreview threw: $it")
                setStatus("camera failed")
            }
    }

    /**
     * Prepare the encoder, once per configuration.
     *
     * Both the auto-preview path (surfaceCreated) and the Start path
     * (onStartStreaming) need the stream prepared before they can do
     * their job, and a prepared encoder must not be prepared again -
     * hence the `prepared` latch. Changing the bandwidth preset or the
     * codec clears it, because those live in `prepareVideo` and cannot
     * be changed under a running encoder; the next Start rebuilds it.
     *
     * The preview is torn down around the call when the frame size is
     * changing under it: the GL surface is sized for the encoder, and
     * reconfiguring it while it is being drawn into is how you get a
     * stretched preview or a dead one. Returns whether the stream is
     * now usable.
     */
    private fun ensurePrepared(): Boolean {
        if (prepared) return true
        setStatus("preparing encoder")
        val wasPreviewing = stream.isOnPreview
        if (wasPreviewing) runCatching { stream.stopPreview() }
        val ok = try {
            // Codec before prepare: it decides which encoder is built.
            // H.265 buys roughly a third off the bitrate for the same
            // picture, but it rides on enhanced RTMP, which not every
            // ingest accepts - so it is opt-in and the operator can try
            // it before a match rather than discover it during one.
            stream.setVideoCodec(
                if (Settings.useHevc(this)) VideoCodec.H265 else VideoCodec.H264
            )
            stream.prepareVideo(
                quality.width, quality.height, quality.bitrate,
                fps = quality.fps,
                iFrameInterval = quality.gop,
            ) &&
                stream.prepareAudio(48_000, true, 128_000)
        } catch (e: Exception) {
            log("prepare threw ${e.javaClass.simpleName}: ${e.message}")
            setStatus("encoder failed"); false
        }
        if (!ok) {
            log("prepareVideo/prepareAudio returned false - try a lower " +
                "bandwidth setting, or H.264 if H.265 is on")
            setStatus("encoder failed")
            // Put the monitor back even though the encoder refused:
            // the operator still needs to see what the camera sees
            // while they pick a setting that works.
            if (wasPreviewing) attachPreview()
            return false
        }
        prepared = true
        if (wasPreviewing) attachPreview()
        log("encoder ready ${quality.width}x${quality.height} @${quality.fps}fps, " +
            "gop ${quality.gop}s, ${if (Settings.useHevc(this)) "H.265" else "H.264"}, " +
            "${quality.label}")
        setStatus("ready")
        return true
    }

    private fun goLiveWithToken(token: String) {
        // Naming comes from the team's settings, which were seeded from
        // VME's own upload templates -- so the stream and the later
        // upload of the same match are named the same way.
        val cfg = TeamStore(this).load(match.team)?.stream ?: StreamConfig()
        val homeName = match.homeRoster.teamName.ifBlank { match.team }
        val fixture = StreamConfig.vars(
            homeName, match.opponent, match.date, match.tournament,
        )
        val title = cfg.render(cfg.titleTemplate, fixture)
        val description = cfg.render(cfg.descriptionTemplate, fixture)
        Thread {
            try {
                setStatus("listing YouTube streams")
                log("live: listing stream keys...")
                val streams = YouTubeLive.listStreams(token)
                val tail = BuildConfig.STREAM_URL.takeLast(4).ifBlank { null }
                val chosen = YouTubeLive.pickStream(streams, tail)
                log("live: using \"${chosen.title}\" (...${chosen.key.takeLast(4)})")

                // A stream key carries one live broadcast at a time,
                // so anything else still running on this channel has
                // to be ended first -- otherwise the key is busy and
                // this match goes nowhere, with nothing on the phone
                // to say why.
                //
                // "Something else" is decided by title, which is
                // opponent plus date: that identifies the fixture
                // even when this install has never seen it (a
                // reinstall, a second phone, or a broadcast left
                // running from the previous match on the court).
                setStatus("checking the channel")
                val active = YouTubeLive.broadcastsWithStatus(token, "active")
                val mine = active.firstOrNull {
                    it.id == match.youtubeBroadcastId || it.title == title
                }
                for (b in active) {
                    if (b.id == mine?.id) continue
                    log("live: ending \"${b.title}\" - not this match")
                    runCatching { YouTubeLive.completeBroadcast(token, b.id) }
                        .onFailure { log("live: could not end it - ${it.message}") }
                }

                // Reuse this match's own broadcast when one is still
                // going. After a dropped connection or a battery
                // swap the right answer is to push back into the
                // same video rather than leave the audience on a
                // dead link. Pressing Stop is the other case: that
                // now completes the broadcast, so this finds nothing
                // and a fresh one is made below.
                val existing = mine?.id
                    ?: match.youtubeBroadcastId?.takeIf { YouTubeLive.isReusable(token, it) }
                val reusing = existing != null

                val broadcast: YouTubeLive.Broadcast
                if (reusing) {
                    setStatus("resuming broadcast")
                    log("live: resuming the broadcast already running for this match")
                    broadcast = YouTubeLive.Broadcast(
                        id = existing!!,
                        title = title,
                        watchUrl = match.youtubeVideoUrl
                            ?: "https://www.youtube.com/watch?v=$existing",
                    )
                } else {
                    if (match.youtubeBroadcastId != null) {
                        log("live: previous broadcast has ended - creating a new one")
                    }
                    setStatus("creating broadcast")
                    log("live: creating broadcast \"$title\"...")
                    broadcast = YouTubeLive.createBroadcast(
                        token = token, title = title,
                        description = description,
                        privacyStatus = "unlisted",
                    )
                }

                // Bind either way. It is idempotent, and a resumed
                // broadcast may have been bound to a different key on
                // a channel with more than one.
                setStatus("binding broadcast")
                YouTubeLive.bind(token, broadcast.id, chosen.id)
                log("live: bound. ${broadcast.watchUrl}")

                match = match.copy(
                    youtubeVideoUrl = broadcast.watchUrl,
                    youtubeBroadcastId = broadcast.id,
                    streamStartedAt = System.currentTimeMillis(),
                )
                store.save(match)

                // Only for a new broadcast: a resumed one already has
                // this thumbnail, and thumbnails.set is 50 quota units
                // and rate-limited per channel.
                if (!reusing) {
                    uploadThumbnail(token, broadcast.id)
                    filePlaylist(token, cfg, broadcast.id)
                    announceLive(token, cfg, title, broadcast.watchUrl)
                }

                val url = "${YouTubeLive.INGEST_PRIMARY}/${chosen.key}"
                runOnUiThread {
                    log("startStream(${url.substringBeforeLast('/')}/***)")
                    setStatus("connecting to RTMP")
                    stream.startStream(url)
                    // From here the broadcast has to survive the
                    // operator locking the phone and Android deciding
                    // a backgrounded process can be frozen. See
                    // StreamService.
                    StreamService.start(
                        this@MatchLiveActivity,
                        match.slug,
                        "${teamName(Side.Home)} vs ${teamName(Side.Away)}",
                    )
                    // Start gives up its slot to the health readout
                    // for the duration -- it is a dead control while
                    // live, and the upload rate is not.
                    startButton.visibility = View.GONE
                    stopButton.isEnabled = true
                    lastBitrateKbps = 0
                    updateHealth()
                }
            } catch (exc: Exception) {
                runOnUiThread {
                    log("live: ${exc.message ?: exc.toString()}")
                    setStatus("YouTube setup failed")
                }
            }
        }.start()
    }

    /**
     * Mark this match's broadcast finished.
     *
     * Broadcasts are created with `enableAutoStop = false`, so
     * dropping the encoder leaves one sitting in `live` -- which is
     * what makes a reconnect resume the same video, and also what
     * makes the stream key unavailable to the next match. Stop is
     * the operator saying they are done, so it is the right moment
     * to release the key.
     *
     * Best effort on a background thread. A broadcast that cannot be
     * completed (expired token, no network at the end of a match)
     * only costs the automatic cleanup -- the next Start ends it as
     * a stale broadcast anyway, and Studio can end it by hand.
     */
    private fun finishBroadcast() {
        val token = accessToken ?: return
        val id = match.youtubeBroadcastId ?: return
        Thread {
            runCatching { YouTubeLive.completeBroadcast(token, id) }
                .onSuccess { runOnUiThread { log("live: broadcast marked finished") } }
                .onFailure {
                    runOnUiThread { log("live: could not finish broadcast - ${it.message}") }
                }
        }.start()
    }

    /**
     * File the broadcast under the team's playlist.
     *
     * Best effort: a playlist that has been deleted, or an id with a
     * typo in it, is not a reason to stop a match going out. The video
     * can be moved by hand, and the log says it needs to be.
     */
    private fun filePlaylist(token: String, cfg: StreamConfig, videoId: String) {
        if (cfg.playlistId.isBlank()) return
        runCatching { YouTubeLive.addToPlaylist(token, cfg.playlistId, videoId) }
            .onSuccess { runOnUiThread { log("live: added to playlist") } }
            .onFailure { runOnUiThread { log("live: playlist failed - ${it.message}") } }
    }

    /**
     * Email whoever follows this team that the broadcast has started.
     *
     * Sent from the phone on the same Google token that created the
     * broadcast, so it works wherever YouTube does -- which is the
     * point, because the announcement matters most at a venue, and a
     * venue is where a server at home is least likely to be reachable.
     *
     * Best effort and off the go-live path: an announcement that did
     * not send is no reason to interrupt a broadcast that did. Each
     * address is tried on its own so one bad entry does not cost the
     * rest, and the outcome is logged, because the operator cannot act
     * on it courtside anyway.
     */
    private fun announceLive(
        token: String, cfg: StreamConfig, title: String, watchUrl: String,
    ) {
        val to = cfg.recipients
        if (to.isEmpty()) return
        // `title` and `url` exist only once the broadcast does, which
        // is why the email templates take a fuller set of placeholders
        // than the ones that produce the title itself.
        val vars = StreamConfig.vars(
            match.homeRoster.teamName.ifBlank { match.team },
            match.opponent, match.date, match.tournament,
            title = title, url = watchUrl,
        )
        val subject = cfg.render(cfg.emailSubjectTemplate, vars)
        val body = cfg.render(cfg.emailBodyTemplate, vars)
        Thread {
            var sent = 0
            val failures = mutableListOf<String>()
            for (address in to) {
                runCatching { Mail.send(token, address, subject, body) }
                    .onSuccess { sent++ }
                    .onFailure { failures.add("$address - ${it.message}") }
            }
            runOnUiThread {
                if (sent > 0) log("live: announced to $sent recipient(s)")
                for (f in failures) log("live: announce failed for $f")
            }
        }.start()
    }

    /**
     * Generate and attach the pre-roll thumbnail.
     *
     * Best effort, on the thread that is already doing YouTube work.
     * A thumbnail that fails to upload must never stop a broadcast
     * starting -- the stream is the thing the operator came for, and
     * YouTube falls back to a frame grab.
     */
    private fun uploadThumbnail(token: String, videoId: String) {
        runCatching {
            val bmp = StreamThumbnail.build(
                homeName = teamName(Side.Home),
                awayName = teamName(Side.Away),
                homeColor = parseColor(match.homeRoster.teamColor, DEFAULT_HOME_COLOR),
                awayColor = parseColor(match.opponentRoster.teamColor, DEFAULT_AWAY_COLOR),
                homeLogo = logoFor(Side.Home),
                awayLogo = logoFor(Side.Away),
            )
            val jpeg = StreamThumbnail.jpeg(bmp)
            bmp.recycle()
            runOnUiThread { setStatus("uploading thumbnail") }
            YouTubeLive.setThumbnail(token, videoId, jpeg)
            runOnUiThread { log("live: thumbnail set (${jpeg.size / 1024} KB)") }
        }.onFailure {
            runOnUiThread { log("live: thumbnail failed - ${it.message}") }
        }
    }

    private fun onStopStreaming() {
        StreamService.stop(this)
        finishBroadcast()
        runCatching { if (stream.isStreaming) stream.stopStream() }
        runCatching { if (stream.isOnPreview) stream.stopPreview() }
        overlay = null
        popup?.hide()
        popup = null
        card = null
        blackout = null
        court = null
        // The filter references are dropped with the overlay; on
        // the next Start a fresh GenericStream + fresh filters are
        // built. `clearFilters` would be belt-and-braces here, but
        // `stopStream` already tears the GL pipeline down.
        runCatching { stream.getGlInterface().clearFilters() }
        wantPreview = false
        startButton.visibility = View.VISIBLE
        startButton.isEnabled = true
        stopButton.isEnabled = false
        lastBitrateKbps = 0
        // Back to advertising the ceiling rather than hiding: the chip
        // is how the bandwidth gets changed before the next Start.
        updateHealth()
        statusLabel.text = "stopped"
        log("stream stopped - match still open, scoring continues")
        setStatus("stopped")
        // Deliberately no export prompt here. Stop ends the *stream*,
        // not the match: the operator may be swapping a battery,
        // moving the tripod at the change of ends, or recovering from
        // a dropped connection, and will hit Start again. The match
        // stays open and scoring keeps working. Export is its own
        // button in the header bar, and the natural moment for it is
        // after Game End.
    }


    // ---- audio routing ---------------------------------------------

    // ---- surviving a lost camera ------------------------------------

    /**
     * Watches the USB port for the capture adapter coming and going.
     *
     * Done here rather than through [UvcVideoSource]'s own callbacks
     * because the moment the camera is lost we swap that source out --
     * and a stopped source has released its helper, so it would never
     * hear the adapter come back. The port is the one thing still
     * watching either way.
     */
    private val usbWatcher = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            val device: UsbDevice? =
                @Suppress("DEPRECATION") intent?.getParcelableExtra(UsbManager.EXTRA_DEVICE)
            if (device == null || !isCaptureDevice(device)) return
            when (intent?.action) {
                UsbManager.ACTION_USB_DEVICE_DETACHED -> onCameraLost()
                UsbManager.ACTION_USB_DEVICE_ATTACHED -> onCameraBack()
            }
        }
    }

    /**
     * Is this the capture adapter rather than some other USB device?
     *
     * By interface class, not by vendor id: the kit bag has been
     * through several adapters and will go through more, and they all
     * advertise a video interface. A charger or a hub going in or out
     * must not take the camera down with it.
     */
    private fun isCaptureDevice(device: UsbDevice): Boolean =
        (0 until device.interfaceCount).any {
            device.getInterface(it).interfaceClass == UsbConstants.USB_CLASS_VIDEO
        }

    /**
     * The adapter is gone: keep the broadcast alive without it.
     *
     * Three things have to happen, and all three are about not losing
     * the stream. The video source becomes a still black frame -- not
     * `NoVideoSource`, which delivers no frames at all and would stall
     * the encoder until YouTube dropped the broadcast. The court view
     * comes up over it, so what goes out is the line-up and the
     * scoreboard rather than a black rectangle. And audio falls back
     * to the phone's own microphone, because the adapter that just
     * left was carrying the sound too.
     */
    private fun onCameraLost() {
        if (cameraLost) return
        cameraLost = true
        log("camera: adapter unplugged")
        if (!stream.isStreaming) {
            setStatus("camera unplugged")
            return
        }
        runCatching { stream.changeVideoSource(blackVideoSource()) }
            .onFailure { log("camera: could not swap the video source: $it") }
        court?.setShown(true)
        pushCourt()
        audioToPhoneMic()
        setStatus("camera lost - court view")
        toast("Camera unplugged - streaming the court view")
    }

    /**
     * The adapter is back: return to the camera and its microphone.
     *
     * A fresh [UvcVideoSource] because the old one was stopped, and a
     * delayed second attempt at the audio routing because the USB
     * audio device is enumerated a moment after the video one -- ask
     * too early and the first attempt finds no USB input and settles
     * for the phone mic for the rest of the match.
     */
    private fun onCameraBack() {
        if (!cameraLost) return
        cameraLost = false
        log("camera: adapter reconnected")
        if (!stream.isStreaming) {
            setStatus("camera reconnected")
            return
        }
        uvcSource = UvcVideoSource(target, ::log)
        runCatching { stream.changeVideoSource(uvcSource) }
            .onFailure { log("camera: could not restore the video source: $it") }
        court?.setShown(quality.court)
        routeAudioToUsbIfPresent()
        window.decorView.postDelayed({
            if (!cameraLost && stream.isStreaming) routeAudioToUsbIfPresent()
        }, USB_AUDIO_SETTLE_MS)
        setStatus("LIVE - streaming")
        toast("Camera back")
    }

    /** A single black frame, redrawn at the encoder's frame rate.
     *  Two pixels: `BitmapSource` scales whatever it is given up to
     *  the encoder size, and a flat colour has nothing to lose. */
    private fun blackVideoSource(): BitmapSource {
        val bmp = android.graphics.Bitmap
            .createBitmap(2, 2, android.graphics.Bitmap.Config.ARGB_8888)
            .also { it.eraseColor(Color.BLACK) }
        return BitmapSource(bmp)
    }

    /**
     * Take the sound from the phone.
     *
     * Explicitly the built-in microphone rather than clearing the
     * preference: with the USB device gone, "no preference" leaves the
     * routing to whatever the system picks next, which on a phone with
     * a headset or a car kit in range is not necessarily the phone.
     */
    private fun audioToPhoneMic() {
        val am = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val mic = am.getDevices(AudioManager.GET_DEVICES_INPUTS)
            .firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_MIC }
        if (mic == null) {
            log("audio: no built-in mic listed - leaving the routing alone")
            return
        }
        val ok = runCatching { micSource.setPreferredDevice(mic) }.getOrElse { false }
        log(if (ok) "audio: switched to the phone mic"
            else "audio: setPreferredDevice(phone mic) returned false")
    }

    private fun routeAudioToUsbIfPresent() {
        val am = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val usb = am.getDevices(AudioManager.GET_DEVICES_INPUTS)
            .firstOrNull { it.type == AudioDeviceInfo.TYPE_USB_DEVICE }
        if (usb == null) { log("audio: no USB input, using phone mic"); return }
        val ok = runCatching { micSource.setPreferredDevice(usb) }.getOrElse { false }
        log(if (ok) "audio: routed to USB \"${usb.productName}\""
            else "audio: setPreferredDevice returned false; re-applied at start()")
    }

    // ---- ConnectChecker --------------------------------------------

    override fun onConnectionStarted(url: String) {
        log("connect: started")
        setStatus("handshaking with RTMP")
    }
    override fun onConnectionSuccess() {
        log("connect: SUCCESS")
        setStatus("LIVE - streaming")
    }
    override fun onConnectionFailed(reason: String) {
        log("connect: FAILED $reason")
        if (stream.getStreamClient().reTry(5000, reason, null)) {
            log("connect: retrying in 5s")
            setStatus("connection failed, retrying")
        } else {
            stream.stopStream()
            log("connect: gave up")
            setStatus("connection failed")
        }
    }
    override fun onDisconnect() {
        log("connect: disconnected")
        setStatus("disconnected")
    }
    override fun onAuthError() {
        log("connect: auth error")
        setStatus("RTMP auth error")
    }
    override fun onAuthSuccess() = log("connect: auth ok")
    override fun onNewBitrate(bitrate: Long) {
        lastBitrateKbps = bitrate / 1000
        runOnUiThread { updateHealth() }
    }

    /**
     * The bandwidth menu, opened by tapping the rate readout.
     *
     * Deliberately reachable mid-stream and with one tap: the moment
     * the operator wants it is the moment the picture starts breaking
     * up, with a rally about to start, and anything buried in a
     * settings screen would not get used.
     */
    private fun chooseQuality() {
        val options = StreamQuality.values()
        val current = options.indexOf(quality)
        val hevc = Settings.useHevc(this)
        AlertDialog.Builder(this)
            .setTitle("Stream bandwidth")
            .setSingleChoiceItems(
                options.map { it.label }.toTypedArray(), current,
            ) { dialog, which ->
                dialog.dismiss()
                applyQuality(options[which])
            }
            // The codec belongs in this menu because it is the same
            // question - how much has to go down the wire - and because
            // it is the one setting here that can stop a stream working
            // at all, so it should be next to the thing it affects
            // rather than buried in Settings.
            .setNeutralButton(if (hevc) "Codec: H.265" else "Codec: H.264") { _, _ ->
                setHevc(!hevc)
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    /**
     * Switch between H.264 and H.265.
     *
     * H.265 sends roughly the same picture in a third less bandwidth,
     * which is the difference between a readable scoreboard and a
     * smear at the bottom of this menu's range. It reaches YouTube
     * over enhanced RTMP, which YouTube accepts and RootEncoder speaks
     * -- but the phone's encoder and the ingest have to agree, and
     * when they do not the stream simply does not come up. Hence a
     * toggle, and hence the warning: try it on a test broadcast first.
     */
    private fun setHevc(on: Boolean) {
        Settings.setUseHevc(this, on)
        val name = if (on) "H.265 (HEVC)" else "H.264"
        if (stream.isStreaming) {
            log("codec -> $name (applies at the next Start)")
            toast("$name at the next Start")
        } else {
            // Force a re-prepare: the codec decides which encoder gets
            // built, so it cannot change under a prepared one.
            prepared = false
            log("codec -> $name")
            toast(name)
        }
        updateHealth()
    }

    /**
     * Take a new ceiling, live if we are live.
     *
     * `setVideoBitrateOnFly` is the one encoder parameter that can be
     * changed without a re-prepare, which is why the presets differ
     * only in bitrate -- see [StreamQuality]. The blackout is a GL
     * filter already in the pipeline, so it flips instantly too.
     * Between them, a stream in trouble can be rescued without
     * stopping it, which is the whole point: stopping means a new
     * YouTube broadcast and a lost audience.
     */
    private fun applyQuality(next: StreamQuality) {
        quality = next
        Settings.setStreamQuality(this, next)
        blackout?.setShown(!next.video)
        // A preset change never takes the court view away while the
        // camera is missing: there would be nothing behind it.
        court?.setShown(next.court || cameraLost)
        if (prepared && stream.isStreaming) {
            // Only the bitrate can move under a running encoder. The
            // frame size, rate and keyframe interval are baked in at
            // prepare, so they wait for the next Start rather than
            // risking a rebuild of a broadcast that is already up.
            runCatching { stream.setVideoBitrateOnFly(next.bitrate) }
                .onFailure { log("bitrate change failed: $it") }
            log("bandwidth -> ${next.label} (bitrate live, " +
                "${next.width}x${next.height}@${next.fps} at the next Start)")
        } else {
            prepared = false
            log("bandwidth -> ${next.label} " +
                "(${next.width}x${next.height}@${next.fps}, gop ${next.gop}s)")
        }
        updateHealth()
        toast(next.label)
    }

    /**
     * Paint the upload rate into the header.
     *
     * Colour-coded against the configured video bitrate rather than
     * an absolute number, because "healthy" depends entirely on what
     * we asked the encoder for. Below half of target sustained means
     * the network is throttling us and the picture is visibly
     * degrading; that is worth a red light courtside, where the
     * operator cannot see the stream itself.
     */
    private fun updateHealth() {
        healthLabel.visibility = View.VISIBLE
        // `prepared` is checked first so this can run from onCreate
        // without forcing the lazy GenericStream into existence.
        if (!prepared || !stream.isStreaming) {
            // Idle: the chip advertises the ceiling that Start will
            // use, and doubles as the way to change it.
            healthLabel.text = "⚙ ${quality.shortLabel}" +
                if (Settings.useHevc(this)) " · H.265" else ""
            healthLabel.setTextColor(Color.parseColor("#888888"))
            return
        }
        val targetKbps = quality.bitrate / 1000
        val kbps = lastBitrateKbps
        val rate = if (kbps >= 1000)
            "%.1f Mbps".format(kbps / 1000f) else "$kbps kbps"
        // The marker says the picture is deliberately black, so a
        // healthy-looking number on a blank stream is not a mystery.
        healthLabel.text = if (quality.video) rate else "$rate ■"
        healthLabel.setTextColor(
            when {
                kbps <= 0 -> Color.parseColor("#FF5252")
                kbps < targetKbps / 2 -> Color.parseColor("#FF5252")
                kbps < targetKbps * 4 / 5 -> Color.parseColor("#FFC107")
                else -> Color.parseColor("#4CAF50")
            }
        )
    }

    // ---- log --------------------------------------------------------

    private fun log(message: String) {
        android.util.Log.i(TAG, message)
        runOnUiThread {
            val ts = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date())
            logView.append("$ts  $message\n")
        }
    }

    @Suppress("unused")
    private fun toast(msg: String) =
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    companion object {
        const val EXTRA_SLUG = "match_slug"
        private const val TAG = "VMEMatch"

        private const val DEFAULT_HOME_COLOR = "#E5484D"
        private const val DEFAULT_AWAY_COLOR = "#8A5CF6"

        /**
         * Button fills, grouped by what the control does.
         *
         * Courtside the operator is looking at the court, not the
         * phone, and reaches for a position before a word -- colour
         * plus emoji makes the group findable in peripheral vision.
         * All dark enough to carry white text at a glance.
         */
        private val BG_ATTACK: Int = Color.parseColor("#7E3B45")
        private val BG_DEFENCE: Int = Color.parseColor("#2F5B7A")
        private val BG_EXTRA: Int = Color.parseColor("#7A5C1E")
        private val BG_SERVE: Int = Color.parseColor("#2E6B45")
        private val BG_NEUTRAL: Int = Color.parseColor("#3A3F4B")
        private val BG_LIFECYCLE: Int = Color.parseColor("#4A3A6B")
        private val BG_LIBERO: Int = Color.parseColor("#1F6B6B")
        /** Cancel: grey rather than red. It backs out of a tap, it
         *  does not delete anything, and a red button on a scoring
         *  surface reads as "undo the last event". */
        private val BG_CANCEL: Int = Color.parseColor("#4A4A52")
        /** Decode size for a court-diagram face. The diagram draws
         *  them at roughly a fifth of the frame height, so this is
         *  generous at 1080p and still a tenth of a raw phone photo. */
        private const val COURT_PHOTO_PX = 256
        /** How far back an event may be stamped from the tap that
         *  finishes it. Long enough for finding a player on a crowded
         *  grid, short enough that an action left armed through a
         *  timeout does not land in the previous rally. */
         private const val MAX_ACTION_BACKDATE_MS = 15_000L
        /** How long the USB audio interface takes to appear after the
         *  video one, replugged. Measured generously: being a second
         *  late to switch back costs nothing, being early costs the
         *  adapter's sound for the rest of the match. */
        private const val USB_AUDIO_SETTLE_MS = 2500L
        /** Rotation slots: darker than the action buttons, because
         *  the grid is a display that happens to be tappable, not a
         *  row of commands competing for the eye. */
        private val BG_SLOT: Int = Color.parseColor("#26292F")
        /** Decode size for a team badge. Generous next to the ~50px
         *  the scoreboard draws it at, so the card -- which draws it
         *  three times larger -- is not working from a blurry
         *  source. */
        private const val LOGO_PX = 256

        /** Held as constants because `refreshAllViews` rewrites both
         *  labels every pass, and a literal in two places drifts. */
        private const val LBL_BALL_SERVED = "\uD83C\uDFD0 Ball Served"
        /** Shown on the armed button during the assist follow-up. */
        private const val LBL_ASSIST = "\uD83E\uDD1D Assist"
        /** The Kill button's resting label. It becomes [LBL_ASSIST]
         *  while the follow-up prompt is open. */
        private const val LBL_KILL = "\uD83D\uDCA5 Kill"
        /** Escape hatch from armed mode, and what it says during the
         *  assist prompt, where cancelling means "unassisted". */
        private const val LBL_CANCEL = "\u2715 Cancel"
        private const val LBL_NO_ASSIST = "\u2715 No assist"
        private const val LBL_TIMEOUT = "\u23F1\uFE0F Timeout"
        private const val LBL_TIMEOUT_END = "\u23F1\uFE0F End Timeout"

        /** Swatches per row in the team-colour grid. */
        private const val SWATCHES_PER_ROW = 6

        /**
         * Swatches offered when naming a team.
         *
         * A fixed grid rather than a colour wheel: the value has to
         * land as a hex string VME will accept, and a wheel asks an
         * operator to fiddle with a gradient between rallies. Six
         * rows of six, one hue family per row -- scan down for the
         * family, across for the shade.
         *
         * The last row is the one that gets used most: club kits are
         * white, black or grey about as often as they are coloured,
         * and a scoreboard with no near-neutral option forces a
         * wrong answer.
         */
        private val TEAM_COLORS = listOf(
            // reds and oranges
            "#E5484D", "#C2313A", "#8E1F26", "#F76B15", "#E8590C", "#B44708",
            // yellows and golds
            "#F5B70A", "#E0A800", "#C9A227", "#8A6D1F", "#D4C04A", "#6B5A12",
            // greens
            "#7CB342", "#4CAF50", "#2E9E4F", "#16794C", "#1B5E20", "#33691E",
            // teals and cyans
            "#4DD0E1", "#26C6DA", "#1F9EA6", "#0E7490", "#00838F", "#00695C",
            // blues
            "#5B8DEF", "#3F7EFF", "#2563EB", "#1D4ED8", "#0B3D91", "#123A6B",
            // purples, pinks and neutrals
            "#A78BFA", "#8A5CF6", "#6D28D9", "#D6409F", "#9D174D", "#4C1D95",
            "#FFFFFF", "#D6DAE0", "#9AA0A6", "#6B7280", "#374151", "#111111",
        )
        /** Spans on a rotation slot's label cover fixed ranges of a
         *  string that is rebuilt every time, so the flag only has to
         *  say "do not extend at the edges". */
        private const val SPAN_FLAGS = Spanned.SPAN_EXCLUSIVE_EXCLUSIVE
        /** Lit rotation slot while an action is armed. */
        private val ARMED_SLOT_BG: Int = Color.parseColor("#2E7D32")
        private val ARMED_SLOT_EMPTY_BG: Int = Color.parseColor("#1B3A1C")

        private val TAB_ACTIVE_BG = Color.parseColor("#2A2A2A")
        private val TAB_IDLE_BG = Color.parseColor("#111111")
    }
}
