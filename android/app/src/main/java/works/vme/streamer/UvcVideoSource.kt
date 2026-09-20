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
 * So: open once with defaults to get the list, then reopen with a chosen
 * entry. Never re-size a running preview.
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
     * Guards the reopen. Without it, closing and reopening inside
     * onCameraOpen recurses forever - and a loop that hammers a USB
     * device is much harder to diagnose than a bad format.
     */
    private var reopened = false

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
        reopened = false
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
            log("device open (first=$isFirstOpen), opening camera with defaults")
            // Deliberately no Size here: the list is not readable until the
            // camera has been opened once.
            cameraHelper?.openCamera()
        }

        override fun onCameraOpen(device: UsbDevice) {
            val helper = cameraHelper ?: return
            val sizes: List<Size> = try {
                helper.supportedSizeList ?: emptyList()
            } catch (e: Exception) {
                log("supportedSizeList threw: $e")
                emptyList()
            }

            if (!reopened) {
                log("supportedSizeList (${sizes.size} entries):")
                sizes.forEach { log("   $it") }
                val current = try {
                    helper.previewSize
                } catch (e: Exception) {
                    log("getPreviewSize threw: $e"); null
                }
                log("default preview size: ${current ?: "(none)"}")

                val pick = choose(sizes, current)
                if (pick != null && pick !== current && pick.toString() != current?.toString()) {
                    reopened = true
                    log("reopening with $pick")
                    try {
                        helper.closeCamera()
                        helper.openCamera(pick)
                        // openCamera re-enters this callback; the rest happens
                        // on that pass.
                        return
                    } catch (e: Exception) {
                        // Falling through to preview at the default size is
                        // better than no video at all - and the log records
                        // that the preferred format was refused.
                        log("reopen failed ($e) - continuing at default")
                    }
                } else {
                    log("default is already the best available; not reopening")
                }
            }

            negotiated = try {
                helper.previewSize?.toString() ?: "(unknown)"
            } catch (_: Exception) {
                "(unknown)"
            }
            log("negotiated: $negotiated")

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
    private fun choose(sizes: List<Size>, current: Size?): Size? {
        if (sizes.isEmpty()) return current
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

    private fun hex(v: Int) = String.format("%04x", v)
}
