package works.vme.streamer

import android.graphics.SurfaceTexture
import android.hardware.usb.UsbDevice
import android.view.Surface
import com.herohan.uvcapp.CameraHelper
import com.herohan.uvcapp.ICameraHelper
import com.pedro.encoder.input.sources.OrientationConfig
import com.pedro.encoder.input.sources.OrientationForced
import com.pedro.encoder.input.sources.video.VideoSource
import com.serenegiant.usb.Size

/**
 * A UVC video source that opens the camera with a format the device
 * actually advertises.
 *
 * RootEncoder ships `CameraUvcSource`, and it is 28 lines: it calls bare
 * `openCamera()` and lets UVCAndroid pick. Critically, its `create()`
 * ignores the width/height/fps you passed to `prepareVideo` - those
 * configure the *encoder*, not the camera, so the stream silently
 * upscales whatever the adapter defaulted to. On an HDMI adapter that
 * default is frequently wrong and sometimes unsupported.
 *
 * The failure mode that costs the evening is this one, reported against
 * HDMI capture cards (UVCAndroid #135, open as of Jan 2026):
 *
 *     setPreviewSize -> IllegalArgumentException: Failed to set preview size
 *     UVCPreview.cpp: could not negotiate with camera: err = -51
 *
 * The library author's own guidance (UVCAndroid #1) is that
 * `setPreviewSize` is for changing format *during* preview and is the
 * wrong tool for choosing one up front - `openCamera(Size)` is the right
 * one, and the Size must be an entry from the enumerated list, used
 * whole. That matters because those cards often advertise 1080p at 60fps
 * only; asking for the same resolution at 30 is what throws.
 *
 * Getting there took two wrong turns, both worth recording.
 *
 * First: open with defaults, then close and reopen with a chosen size.
 * That cannot work. `closeCamera` and `openCamera` both post to the
 * helper's async handler, and closing the camera also closes the device,
 * so the queued open found `mUsbDevice` gone and silently did nothing.
 * On hardware the log ended at "device closed" with no preview and no
 * error.
 *
 * Second: choose the format in `onDeviceOpen`, before opening. Also
 * wrong - `CameraInternal.getSupportedSizeList()` returns null unless
 * `mUVCCamera != null`, which only happens once the camera is open. On
 * hardware that logged "no advertised sizes" and ran at the library's
 * default, which on this adapter is 2560x1440 - 1440p over a USB 2.0 bus
 * to feed a 1080p encoder.
 *
 * So the list is read in `onCameraOpen`, where it exists, and the format
 * is applied two ways:
 *
 *  - `setPreviewSize` on the open camera, which is what the library
 *    itself offers for this, and
 *  - the choice is remembered, so the *next* open can pass it straight to
 *    `openCamera(Size)` - the author-sanctioned path, with no resize at
 *    all.
 *
 * The remembered value is what makes this safe: `setPreviewSize` destroys
 * the camera if the format is refused (see `CameraInternal`), so the
 * second route exists to avoid needing it.
 */
class UvcVideoSource(
    private val target: Target,
    private val log: (String) -> Unit,
) : VideoSource() {

    /**
     * What to ask the adapter for. Resolution is a preference, not a
     * demand - an adapter that cannot do it should stream something
     * rather than nothing, and the log says what it settled on.
     */
    data class Target(
        val width: Int = 1920,
        val height: Int = 1080,
        /**
         * Frames per second to request, if the chosen entry offers it in
         * its `fpsList`.
         *
         * Worth insisting on: an HDMI adapter's default for 1080p MJPEG is
         * often the highest it can name (50 or 60), and on a USB 2.0 bus
         * that spends bandwidth on frames nobody asked for - the encoder
         * is configured for 30, so the surplus is decoded and thrown away.
         */
        val fps: Int = 30,
        /**
         * UVC frame type to prefer. 7 is MJPEG for this library's Size.type.
         * Null means no preference.
         *
         * MJPEG matters on USB 2.0 adapters: uncompressed 1080p needs more
         * bandwidth than the bus has, so those entries exist at ~5fps and
         * are useless for video.
         */
        val preferType: Int? = 7,
    )

    private var cameraHelper: ICameraHelper? = null
    private var surface: Surface? = null
    private var running = false

    /**
     * The format chosen on a previous open, reused on the next one.
     *
     * Held in memory rather than persisted: it survives stop/start of the
     * source, which is all that is needed, and a stale entry cannot then
     * outlive a change of adapter.
     */
    private var remembered: Size? = null

    /** What the camera actually ended up running at. */
    @Volatile
    var negotiated: String = "(not open)"
        private set

    override fun create(width: Int, height: Int, fps: Int, rotation: Int): Boolean {
        // These describe the encoder, not the camera. Said out loud because
        // the mismatch is the whole point of this class.
        log("encoder wants ${width}x$height@$fps rot=$rotation " +
            "(camera negotiates separately)")
        return true
    }

    override fun start(surfaceTexture: SurfaceTexture) {
        this.surfaceTexture = surfaceTexture
        if (isRunning()) return
        surface = Surface(surfaceTexture)
        cameraHelper = CameraHelper().apply { setStateCallback(stateCallback) }
        running = true
        log("UVC source started, waiting for device attach...")
    }

    override fun stop() {
        surface?.let { cameraHelper?.removeSurface(it) }
        surface?.release()
        surface = null
        cameraHelper?.release()
        cameraHelper = null
        running = false
        negotiated = "(not open)"
    }

    override fun release() = Unit

    override fun isRunning(): Boolean = running

    // HDMI capture is always landscape; letting the phone's rotation drive
    // it would rotate the court.
    override fun getOrientationConfig() =
        OrientationConfig(forced = OrientationForced.LANDSCAPE)

    private val stateCallback = object : ICameraHelper.StateCallback {

        override fun onAttach(device: UsbDevice) {
            log("attach ${hex(device.vendorId)}:${hex(device.productId)} " +
                "${device.productName ?: ""}")
            cameraHelper?.selectDevice(device)
        }

        override fun onDeviceOpen(device: UsbDevice, isFirstOpen: Boolean) {
            val helper = cameraHelper ?: return
            log("device open (first=$isFirstOpen)")

            // The size list is NOT readable yet - it comes off the open
            // camera. But if a previous open already told us what this
            // adapter offers, it can be applied here, which is the one path
            // that never resizes a live camera.
            val known = remembered
            if (known == null) {
                log("opening camera with defaults (format list needs an open camera)")
                helper.openCamera()
            } else {
                log("opening camera directly with remembered $known")
                try {
                    helper.openCamera(known)
                } catch (e: Exception) {
                    log("openCamera($known) threw: $e - falling back to defaults")
                    helper.openCamera()
                }
            }
        }

        override fun onCameraOpen(device: UsbDevice) {
            val helper = cameraHelper ?: return

            val sizes: List<Size> = try {
                helper.supportedSizeList ?: emptyList()
            } catch (e: Exception) {
                log("supportedSizeList threw: $e")
                emptyList()
            }

            val current = try {
                helper.previewSize
            } catch (e: Exception) {
                log("getPreviewSize threw: $e"); null
            }
            log("camera open at ${current ?: "(unknown)"}, " +
                "${sizes.size} advertised formats")

            if (remembered == null && sizes.isNotEmpty()) {
                sizes.forEach { log("   $it") }
                val pick = choose(sizes)?.let { atTargetFps(it) }
                if (pick != null) {
                    remembered = pick
                    if (sameFormat(pick, current)) {
                        log("already at $pick")
                    } else {
                        // The library's own way to change format on an open
                        // camera. It is not free: CameraInternal destroys the
                        // camera if the device refuses, so the result is
                        // checked rather than assumed.
                        log("applying $pick via setPreviewSize")
                        try {
                            helper.setPreviewSize(pick)
                        } catch (e: Exception) {
                            log("setPreviewSize threw: $e")
                        }
                    }
                }
            }

            negotiated = try {
                helper.previewSize?.toString() ?: "(unknown)"
            } catch (_: Exception) {
                "(unknown)"
            }
            log("negotiated: $negotiated")
            if (remembered != null && !sameFormat(remembered!!, helper.runCatching { previewSize }.getOrNull())) {
                log("note: setPreviewSize is asynchronous, so the line above may " +
                    "still show the old format. Stop and start preview to open " +
                    "directly at ${remembered}.")
            }

            // Order matters: add the surface before starting preview so the
            // first frames have somewhere to land.
            surface?.let { helper.addSurface(it, false) }
            helper.startPreview()
            log("startPreview() returned - if the view stays black, check " +
                "logcat for 'could not negotiate with camera'")
        }

        override fun onCameraClose(device: UsbDevice) = Unit
        override fun onDeviceClose(device: UsbDevice) = log("device closed")
        override fun onDetach(device: UsbDevice) = log("detached")
        override fun onCancel(device: UsbDevice) =
            log("permission cancelled - the app cannot open the adapter")
    }

    /**
     * Pick the best advertised entry for the target.
     *
     * Exact resolution wins; otherwise the largest that does not exceed
     * the target, because upscaling a smaller capture is honest while
     * downscaling a larger one wastes USB bandwidth we may not have.
     */
    private fun choose(sizes: List<Size>): Size? {
        if (sizes.isEmpty()) return null
        val preferred = target.preferType?.let { t -> sizes.filter { it.type == t } }
            ?.takeIf { it.isNotEmpty() }
            ?: sizes
        if (target.preferType != null && preferred.size == sizes.size) {
            log("note: every entry is type ${target.preferType}")
        }

        preferred.firstOrNull { it.width == target.width && it.height == target.height }
            ?.let { return it }

        val area = target.width.toLong() * target.height
        val under = preferred
            .filter { it.width.toLong() * it.height <= area }
            .maxByOrNull { it.width.toLong() * it.height }
        if (under != null) {
            log("no ${target.width}x${target.height}; largest that fits is $under")
            return under
        }
        val smallest = preferred.minByOrNull { it.width.toLong() * it.height }
        log("everything exceeds the target; taking smallest: $smallest")
        return smallest
    }

    /**
     * Return the same entry at the target frame rate if it offers one.
     *
     * A `Size` carries both a single `fps` - whatever the library chose to
     * surface, typically the highest - and the full `fpsList` from the
     * descriptor. Asking for a rate that is not in that list is what
     * produces "could not negotiate with camera", so this only ever picks
     * from the list and otherwise leaves the entry untouched.
     */
    private fun atTargetFps(size: Size): Size {
        val rates = size.fpsList ?: emptyList()
        if (size.fps == target.fps) return size
        if (!rates.contains(target.fps)) {
            log("${size.width}x${size.height} does not offer ${target.fps}fps " +
                "(has $rates) - taking it at ${size.fps}")
            return size
        }
        log("requesting ${target.fps}fps instead of ${size.fps} (available: $rates)")
        return Size(size.type, size.width, size.height, target.fps, rates)
    }

    /** Size has no useful equals(), and identity is never right here. */
    private fun sameFormat(a: Size, b: Size?): Boolean =
        b != null && a.type == b.type && a.width == b.width &&
            a.height == b.height && a.fps == b.fps

    private fun hex(v: Int) = String.format("%04x", v)
}
