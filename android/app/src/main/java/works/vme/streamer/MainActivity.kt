package works.vme.streamer

import android.Manifest
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Color
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.text.method.ScrollingMovementMethod
import android.view.Gravity
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.pedro.common.ConnectChecker
import com.pedro.encoder.input.sources.audio.MicrophoneSource
import com.pedro.library.generic.GenericStream
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Step 0 + step 2 spike harness for docs/android-streaming-spike.md.
 *
 * This is not the app. It is the instrument that decides whether the app
 * is buildable at all, and its only real output is the text in the log
 * pane, which goes into the Findings section of the spike doc.
 *
 * The four buttons run in order and each one can fail independently,
 * which is the point - "it doesn't work" is not a finding, but "the
 * descriptor advertises MJPEG 1080p60 only, and libuvc fails to
 * negotiate it" is.
 *
 *   1 PROBE   - read USB descriptors and audio devices. No libraries
 *               involved, so it answers the hardware questions even if
 *               everything else fails.
 *   2 PREVIEW - open the adapter through UVCAndroid and render it.
 *               This is the step 0 exit criterion.
 *   3 STREAM  - push to an RTMP(S) URL. The step 2 exit criterion.
 *   COPY      - put the whole log on the clipboard to paste into the doc.
 */
class MainActivity : ComponentActivity(), ConnectChecker {

    private lateinit var logView: TextView
    private lateinit var surfaceView: SurfaceView
    private lateinit var urlInput: EditText

    private val target = UvcVideoSource.Target(width = 1920, height = 1080)
    private val uvcSource by lazy { UvcVideoSource(target, ::log) }
    private val micSource by lazy { MicrophoneSource() }

    private val stream by lazy {
        GenericStream(this, this, uvcSource, micSource)
    }

    /** Preview is only attached once the surface exists AND we asked for it. */
    private var wantPreview = false
    private var surfaceReady = false

    // ---- lifecycle --------------------------------------------------------

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(buildUi())
        registerUsbReceiver()

        log("VME streamer spike harness")
        log("device: ${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})")
        log("Press 1 PROBE with the HDMI adapter plugged in.")
        log("")

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.RECORD_AUDIO), 1)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        runCatching { unregisterReceiver(usbReceiver) }
        runCatching { if (stream.isStreaming) stream.stopStream() }
        runCatching { if (stream.isOnPreview) stream.stopPreview() }
        runCatching { stream.release() }
    }

    // ---- UI ---------------------------------------------------------------

    private fun buildUi(): ViewGroup {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.BLACK)
        }

        val buttons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        buttons.addView(button("1 PROBE") { probe() })
        buttons.addView(button("2 PREVIEW") { startPreview() })
        buttons.addView(button("3 STREAM") { toggleStream() })
        buttons.addView(button("COPY") { copyLog() })
        buttons.addView(button("CLEAR") { logView.text = "" })
        root.addView(HorizontalScrollView(this).apply { addView(buttons) })

        urlInput = EditText(this).apply {
            hint = "rtmps://a.rtmps.youtube.com:443/live2/STREAM-KEY"
            inputType = InputType.TYPE_TEXT_VARIATION_URI
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
        }
        root.addView(urlInput)

        surfaceView = SurfaceView(this)
        surfaceView.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(holder: SurfaceHolder) {
                surfaceReady = true
                if (wantPreview) attachPreview()
            }

            override fun surfaceChanged(h: SurfaceHolder, f: Int, w: Int, hgt: Int) {
                if (stream.isOnPreview) stream.getGlInterface().setPreviewResolution(w, hgt)
            }

            override fun surfaceDestroyed(holder: SurfaceHolder) {
                surfaceReady = false
                if (stream.isOnPreview) stream.stopPreview()
            }
        })
        root.addView(surfaceView, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0
        ).apply { weight = 1f })

        logView = TextView(this).apply {
            setTextColor(Color.parseColor("#8FE38F"))
            textSize = 11f
            typeface = android.graphics.Typeface.MONOSPACE
            movementMethod = ScrollingMovementMethod()
            setPadding(8, 8, 8, 8)
        }
        root.addView(android.widget.ScrollView(this).apply { addView(logView) },
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0)
                .apply { weight = 2f })

        return root
    }

    private fun button(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        gravity = Gravity.CENTER
        setOnClickListener { onClick() }
    }

    // ---- step 1: probe ----------------------------------------------------

    private fun probe() {
        log("--- USB devices ---")
        log(UsbProbe.listDevices(this))

        val manager = getSystemService(Context.USB_SERVICE) as UsbManager
        val video = manager.deviceList.values.firstOrNull { d ->
            (0 until d.interfaceCount).any { d.getInterface(it).interfaceClass == 0x0E }
        }
        if (video == null) {
            log("No UVC device found. Everything past this point will fail.")
        } else if (!manager.hasPermission(video)) {
            log("Requesting USB permission...")
            requestUsbPermission(manager, video)
            return
        } else {
            log("--- video formats ---")
            log(UsbProbe.dumpVideoFormats(this, video))
        }

        log("--- audio inputs ---")
        log(UsbProbe.dumpAudioInputs(this))
    }

    private fun requestUsbPermission(manager: UsbManager, device: UsbDevice) {
        // FLAG_MUTABLE is required: the system writes EXTRA_PERMISSION_GRANTED
        // into this intent, and an immutable one silently never fires on
        // Android 14.
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            PendingIntent.FLAG_MUTABLE
        } else {
            0
        }
        val intent = PendingIntent.getBroadcast(
            this, 0, Intent(ACTION_USB_PERMISSION).setPackage(packageName), flags
        )
        manager.requestPermission(device, intent)
    }

    private val usbReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action != ACTION_USB_PERMISSION) return
            val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
            log(if (granted) "USB permission granted - press 1 PROBE again."
            else "USB permission DENIED. Nothing can open the adapter.")
        }
    }

    private fun registerUsbReceiver() {
        val filter = IntentFilter(ACTION_USB_PERMISSION)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(usbReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(usbReceiver, filter)
        }
    }

    // ---- step 2: preview --------------------------------------------------

    private fun startPreview() {
        if (stream.isOnPreview) {
            log("already previewing")
            return
        }
        routeAudioToUsbIfPresent()

        // The encoder is configured here; the camera negotiates separately
        // inside UvcVideoSource. A mismatch between the two is expected and
        // is exactly what the log is for.
        // prepareVideo throws IllegalArgumentException for an unsupported
        // configuration and IllegalStateException if anything is already
        // running, and the difference matters when reading the log.
        val prepared = try {
            stream.prepareVideo(target.width, target.height, 6_000_000, fps = 30) &&
                stream.prepareAudio(48_000, true, 128_000)
        } catch (e: Exception) {
            log("prepare threw ${e.javaClass.simpleName}: ${e.message}")
            false
        }
        if (!prepared) {
            log("prepareVideo/prepareAudio returned false - the phone's encoder " +
                "refused this configuration. Try 1280x720 or 3 Mbps.")
            return
        }
        stream.getStreamClient().setReTries(10)

        wantPreview = true
        if (surfaceReady) attachPreview() else log("waiting for surface...")
    }

    private fun attachPreview() {
        log("startPreview()")
        runCatching { stream.startPreview(surfaceView) }
            .onFailure { log("startPreview threw: $it") }
    }

    /**
     * HDMI capture audio is a separate USB audio device, so the microphone
     * source has to be pointed at it explicitly. If there isn't one, the
     * phone mic is used and the log says so - in a gym that is a defensible
     * outcome, not a failure.
     */
    private fun routeAudioToUsbIfPresent() {
        val am = getSystemService(Context.AUDIO_SERVICE) as android.media.AudioManager
        val usb = am.getDevices(android.media.AudioManager.GET_DEVICES_INPUTS)
            .firstOrNull { it.type == android.media.AudioDeviceInfo.TYPE_USB_DEVICE }
        if (usb == null) {
            log("audio: no USB input, using phone mic")
            return
        }
        val ok = runCatching { micSource.setPreferredDevice(usb) }.getOrElse {
            log("audio: setPreferredDevice threw: $it"); false
        }
        log("audio: routing to USB \"${usb.productName}\" -> $ok")
    }

    // ---- step 3: stream ---------------------------------------------------

    private fun toggleStream() {
        if (stream.isStreaming) {
            stream.stopStream()
            log("stopStream()")
            return
        }
        val url = urlInput.text.toString().trim()
        if (url.isEmpty()) {
            toast("Paste an RTMP/RTMPS URL first")
            return
        }
        if (!stream.isOnPreview) {
            log("not previewing yet - press 2 PREVIEW first so the camera is open")
            return
        }
        log("startStream(${url.substringBeforeLast('/')}/***)")
        stream.startStream(url)
    }

    // ---- ConnectChecker ---------------------------------------------------

    override fun onConnectionStarted(url: String) = log("connect: started")
    override fun onConnectionSuccess() = log("connect: SUCCESS")
    override fun onConnectionFailed(reason: String) {
        log("connect: FAILED $reason")
        if (stream.getStreamClient().reTry(5000, reason, null)) {
            log("connect: retrying in 5s")
        } else {
            stream.stopStream()
            log("connect: gave up")
        }
    }

    override fun onDisconnect() = log("connect: disconnected")
    override fun onAuthError() = log("connect: auth error")
    override fun onAuthSuccess() = log("connect: auth ok")
    override fun onNewBitrate(bitrate: Long) {
        // Once a second; only worth seeing while streaming.
        if (stream.isStreaming) log("bitrate ${bitrate / 1000} kbps")
    }

    // ---- logging ----------------------------------------------------------

    private fun copyLog() {
        val text = logView.text.toString()
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("vme spike log", text))
        // Also drop a file, because a long log is awkward to paste off a
        // phone and this one is meant to end up in a markdown doc.
        runCatching {
            val f = File(getExternalFilesDir(null), "spike-${stamp()}.txt")
            f.writeText(text)
            toast("Copied. Also wrote ${f.absolutePath}")
        }.onFailure { toast("Copied to clipboard") }
    }

    private fun stamp() = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date())

    private fun log(message: String) {
        android.util.Log.i(TAG, message)
        runOnUiThread {
            logView.append(message)
            logView.append("\n")
        }
    }

    private fun toast(message: String) =
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()

    companion object {
        private const val TAG = "VMESpike"
        private const val ACTION_USB_PERMISSION = "works.vme.streamer.USB_PERMISSION"
    }
}
