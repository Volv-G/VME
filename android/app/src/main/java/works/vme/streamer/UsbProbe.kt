package works.vme.streamer

import android.content.Context
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbManager
import android.media.AudioDeviceInfo
import android.media.AudioManager

/**
 * Reads what the HDMI capture adapter actually advertises, straight from
 * its USB descriptors.
 *
 * Why this exists instead of just asking UVCAndroid: when libuvc fails
 * with `could not negotiate with camera: err = -51`, you cannot tell from
 * that message whether the adapter lacks the format we want or whether
 * the library is mis-negotiating a format that is right there in the
 * descriptor. Those two have completely different fixes - buy a different
 * adapter, versus pick a different entry from the list - and guessing
 * wrong costs an evening.
 *
 * The descriptors are the ground truth. This parses them with no library
 * in the way, so the Findings section gets a fact rather than an inference.
 *
 * References: USB Device Class Definition for Video Devices 1.5,
 * tables 3-1 (VS interface), 3-11 (uncompressed frame), 3-2 (MJPEG frame).
 */
object UsbProbe {

    // Class codes we care about naming in the report.
    private const val CLASS_VIDEO = 0x0E
    private const val CLASS_AUDIO = 0x01

    private const val DESC_DEVICE = 0x01
    private const val DESC_CONFIG = 0x02
    private const val DESC_INTERFACE = 0x04
    private const val DESC_CS_INTERFACE = 0x24

    private const val SC_VIDEOSTREAMING = 0x02

    // VideoStreaming descriptor subtypes.
    private const val VS_FORMAT_UNCOMPRESSED = 0x04
    private const val VS_FRAME_UNCOMPRESSED = 0x05
    private const val VS_FORMAT_MJPEG = 0x06
    private const val VS_FRAME_MJPEG = 0x07
    private const val VS_FORMAT_FRAME_BASED = 0x10
    private const val VS_FRAME_FRAME_BASED = 0x11

    data class FrameFormat(
        val format: String,
        val formatIndex: Int,
        val frameIndex: Int,
        val width: Int,
        val height: Int,
        /** Frame rates the descriptor lists, highest first. */
        val fps: List<Double>,
    ) {
        override fun toString(): String {
            val rates = fps.joinToString(", ") { fmtFps(it) }
            return "  $format  ${width}x$height  @ [$rates]" +
                "   (format $formatIndex, frame $frameIndex)"
        }
    }

    /** Human summary of every USB device currently attached. */
    fun listDevices(context: Context): String {
        val manager = context.getSystemService(Context.USB_SERVICE) as UsbManager
        val devices = manager.deviceList.values
        if (devices.isEmpty()) {
            return "No USB devices attached.\n" +
                "If the adapter is plugged in, the phone may not be supplying " +
                "OTG power - check the cable and the powerbank."
        }
        val sb = StringBuilder()
        for (d in devices) {
            sb.append(describe(d)).append('\n')
        }
        return sb.toString()
    }

    /** Vendor/product ids and the interface map, without opening the device. */
    fun describe(device: UsbDevice): String {
        val sb = StringBuilder()
        sb.append("Device ").append(hex4(device.vendorId)).append(':')
            .append(hex4(device.productId)).append('\n')
        sb.append("  name        ").append(device.deviceName).append('\n')
        sb.append("  manufacturer ").append(device.manufacturerName ?: "?").append('\n')
        sb.append("  product     ").append(device.productName ?: "?").append('\n')
        sb.append("  serial      ").append(safeSerial(device)).append('\n')
        sb.append("  class       ").append(className(device.deviceClass)).append('\n')
        sb.append("  interfaces  ").append(device.interfaceCount).append('\n')
        var hasVideo = false
        var hasAudio = false
        for (i in 0 until device.interfaceCount) {
            val iface = device.getInterface(i)
            if (iface.interfaceClass == CLASS_VIDEO) hasVideo = true
            if (iface.interfaceClass == CLASS_AUDIO) hasAudio = true
            sb.append("    [").append(i).append("] class=")
                .append(className(iface.interfaceClass))
                .append(" subclass=").append(iface.interfaceSubclass)
                .append(" endpoints=").append(iface.endpointCount)
                .append('\n')
        }
        // This is the single most useful line in the whole probe: it settles
        // whether HDMI audio can come off the adapter at all, before any
        // time is spent on AudioRecord routing.
        sb.append("  => UVC video: ").append(if (hasVideo) "YES" else "NO").append('\n')
        sb.append("  => UAC audio: ").append(if (hasAudio) "YES" else "NO")
        if (!hasAudio) {
            sb.append("  (adapter carries no audio interface - HDMI audio ")
                .append("cannot be captured from it; use the phone mic)")
        }
        sb.append('\n')
        return sb.toString()
    }

    /**
     * Parse the raw configuration descriptors for the video formats.
     *
     * Requires USB permission for the device - call after the permission
     * dialog has been accepted, or this returns an explanatory string.
     */
    fun dumpVideoFormats(context: Context, device: UsbDevice): String {
        val manager = context.getSystemService(Context.USB_SERVICE) as UsbManager
        if (!manager.hasPermission(device)) {
            return "No USB permission for ${hex4(device.vendorId)}:" +
                "${hex4(device.productId)} yet - grant it and probe again."
        }
        var conn: UsbDeviceConnection? = null
        return try {
            conn = manager.openDevice(device)
                ?: return "openDevice() returned null - another app may hold " +
                    "the adapter. Close USB Camera Pro and retry."
            val raw = conn.rawDescriptors
                ?: return "getRawDescriptors() returned null."
            renderFormats(parseFormats(raw), raw.size)
        } catch (e: Exception) {
            "Descriptor parse failed: $e"
        } finally {
            conn?.close()
        }
    }

    private fun renderFormats(formats: List<FrameFormat>, rawSize: Int): String {
        if (formats.isEmpty()) {
            return "Parsed $rawSize descriptor bytes but found no VideoStreaming " +
                "frame descriptors.\nThat is itself a finding: the adapter is not " +
                "advertising formats the normal way."
        }
        val sb = StringBuilder()
        sb.append("Advertised video formats (").append(formats.size)
            .append(" entries, from ").append(rawSize).append(" descriptor bytes):\n")
        for (f in formats) sb.append(f).append('\n')

        // Call out the specific combination step 2 wants, because scanning
        // the list by eye for it is exactly the mistake that wastes the
        // evening.
        val want = formats.filter { it.width == 1920 && it.height == 1080 }
        sb.append('\n')
        if (want.isEmpty()) {
            sb.append("!! No 1920x1080 entry at all. 1080p is not on the table ")
                .append("with this adapter.\n")
        } else {
            val has30 = want.any { f -> f.fps.any { kotlin.math.abs(it - 30.0) < 1.0 } }
            val mjpeg = want.any { it.format.contains("MJPEG") }
            sb.append("1080p entries: ").append(want.size)
                .append(", MJPEG present: ").append(if (mjpeg) "yes" else "NO")
                .append(", 30fps offered: ").append(if (has30) "yes" else "NO")
                .append('\n')
            if (!has30) {
                sb.append("   Note: only 60fps is advertised at 1080p. Asking for ")
                    .append("30 is what throws\n   'Failed to set preview size' - ")
                    .append("open with a listed entry instead of re-sizing.\n")
            }
            if (!mjpeg) {
                sb.append("   Note: uncompressed 1080p over USB 2.0 is ~5fps of ")
                    .append("bandwidth. If MJPEG\n   is absent this adapter cannot ")
                    .append("do usable 1080p - that is a hardware answer.\n")
            }
        }
        return sb.toString()
    }

    /** Walk the TLV descriptor blob and pull out format/frame pairs. */
    fun parseFormats(raw: ByteArray): List<FrameFormat> {
        val out = ArrayList<FrameFormat>()
        var i = 0
        var inVideoStreaming = false
        var currentFormat = ""
        var currentFormatIndex = 0

        while (i + 1 < raw.size) {
            val len = raw[i].toInt() and 0xFF
            val type = raw[i + 1].toInt() and 0xFF
            // A zero length would spin forever; a length past the end means
            // the blob is truncated. Either way, stop rather than guess.
            if (len < 2 || i + len > raw.size) break

            when (type) {
                DESC_DEVICE, DESC_CONFIG -> Unit
                DESC_INTERFACE -> {
                    val cls = raw[i + 5].toInt() and 0xFF
                    val sub = raw[i + 6].toInt() and 0xFF
                    inVideoStreaming = cls == CLASS_VIDEO && sub == SC_VIDEOSTREAMING
                }
                DESC_CS_INTERFACE -> if (inVideoStreaming && len >= 3) {
                    when (raw[i + 2].toInt() and 0xFF) {
                        VS_FORMAT_MJPEG -> {
                            currentFormat = "MJPEG"
                            currentFormatIndex = raw[i + 3].toInt() and 0xFF
                        }
                        VS_FORMAT_UNCOMPRESSED -> {
                            currentFormatIndex = raw[i + 3].toInt() and 0xFF
                            currentFormat = "UNCOMPRESSED" + guidSuffix(raw, i, len)
                        }
                        VS_FORMAT_FRAME_BASED -> {
                            currentFormatIndex = raw[i + 3].toInt() and 0xFF
                            currentFormat = "FRAME_BASED" + guidSuffix(raw, i, len)
                        }
                        VS_FRAME_MJPEG, VS_FRAME_UNCOMPRESSED, VS_FRAME_FRAME_BASED ->
                            parseFrame(raw, i, len, currentFormat, currentFormatIndex)
                                ?.let { out.add(it) }
                    }
                }
            }
            i += len
        }
        return out
    }

    /**
     * Frame descriptors are identical in layout for MJPEG and uncompressed;
     * frame-based inserts a 4-byte dwBytesPerLine before the interval block.
     */
    private fun parseFrame(
        raw: ByteArray,
        off: Int,
        len: Int,
        format: String,
        formatIndex: Int,
    ): FrameFormat? {
        if (len < 26) return null
        val subtype = raw[off + 2].toInt() and 0xFF
        val frameIndex = raw[off + 3].toInt() and 0xFF
        val width = u16(raw, off + 5)
        val height = u16(raw, off + 7)

        var p = off + 25
        if (subtype == VS_FRAME_FRAME_BASED) p += 4
        if (p >= off + len) return null
        val intervalType = raw[p].toInt() and 0xFF
        p += 1

        val rates = ArrayList<Double>()
        if (intervalType == 0) {
            // Continuous: min/max/step, in 100ns units. Report the endpoints;
            // the step is rarely interesting and always noisy to print.
            if (p + 8 <= off + len) {
                val minI = u32(raw, p)
                val maxI = u32(raw, p + 4)
                if (minI > 0) rates.add(1e7 / minI)
                if (maxI > 0 && maxI != minI) rates.add(1e7 / maxI)
            }
        } else {
            for (n in 0 until intervalType) {
                val q = p + n * 4
                if (q + 4 > off + len) break
                val interval = u32(raw, q)
                if (interval > 0) rates.add(1e7 / interval)
            }
        }
        rates.sortDescending()
        return FrameFormat(format, formatIndex, frameIndex, width, height, rates)
    }

    /** Uncompressed/frame-based formats identify themselves by GUID. */
    private fun guidSuffix(raw: ByteArray, off: Int, len: Int): String {
        if (len < 21) return ""
        // First four bytes of the GUID are the FourCC for the common ones.
        val cc = CharArray(4)
        for (n in 0 until 4) {
            val c = raw[off + 5 + n].toInt() and 0xFF
            cc[n] = if (c in 32..126) c.toChar() else '?'
        }
        return "(" + String(cc).trim() + ")"
    }

    // ---- audio ------------------------------------------------------------

    /** Every USB audio input Android can see, with its capture parameters. */
    fun dumpAudioInputs(context: Context): String {
        val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val devices = am.getDevices(AudioManager.GET_DEVICES_INPUTS)
        val usb = devices.filter {
            it.type == AudioDeviceInfo.TYPE_USB_DEVICE ||
                it.type == AudioDeviceInfo.TYPE_USB_HEADSET ||
                it.type == AudioDeviceInfo.TYPE_USB_ACCESSORY
        }
        val sb = StringBuilder()
        sb.append("Audio inputs: ").append(devices.size)
            .append(" total, ").append(usb.size).append(" USB\n")
        if (usb.isEmpty()) {
            sb.append("No USB audio input. HDMI audio is a separate UAC device ")
                .append("from the video\ninterface - if it is absent here, the ")
                .append("adapter does not expose it and the\nstream has to take ")
                .append("audio from the phone mic. For a gym that is arguably\n")
                .append("better anyway: crowd noise beats a line feed of nothing.\n")
            return sb.toString()
        }
        for (d in usb) {
            sb.append("  id=").append(d.id)
                .append(" \"").append(d.productName).append("\"\n")
            sb.append("    channels ").append(d.channelCounts.joinToString())
                .append("\n    rates    ").append(d.sampleRates.joinToString())
                .append("\n    encodings ").append(d.encodings.joinToString())
                .append('\n')
            // 48k stereo is what prepareAudio asks for; say plainly whether
            // it is available rather than leaving it to be read off the list.
            val ok48 = d.sampleRates.isEmpty() || d.sampleRates.contains(48000)
            val okStereo = d.channelCounts.isEmpty() || d.channelCounts.contains(2)
            sb.append("    => 48kHz: ").append(if (ok48) "yes" else "NO")
                .append(", stereo: ").append(if (okStereo) "yes" else "NO")
                .append('\n')
        }
        return sb.toString()
    }

    // ---- helpers ----------------------------------------------------------

    private fun safeSerial(device: UsbDevice): String = try {
        device.serialNumber ?: "?"
    } catch (_: SecurityException) {
        "(needs permission)"
    }

    private fun className(c: Int): String = when (c) {
        UsbConstants.USB_CLASS_PER_INTERFACE -> "per-interface($c)"
        CLASS_AUDIO -> "AUDIO($c)"
        CLASS_VIDEO -> "VIDEO($c)"
        UsbConstants.USB_CLASS_HID -> "HID($c)"
        UsbConstants.USB_CLASS_MISC -> "MISC/IAD($c)"
        else -> "class($c)"
    }

    private fun u16(b: ByteArray, i: Int): Int =
        (b[i].toInt() and 0xFF) or ((b[i + 1].toInt() and 0xFF) shl 8)

    private fun u32(b: ByteArray, i: Int): Long =
        (b[i].toLong() and 0xFF) or
            ((b[i + 1].toLong() and 0xFF) shl 8) or
            ((b[i + 2].toLong() and 0xFF) shl 16) or
            ((b[i + 3].toLong() and 0xFF) shl 24)

    private fun hex4(v: Int): String = String.format("%04x", v)

    private fun fmtFps(v: Double): String =
        if (kotlin.math.abs(v - Math.round(v)) < 0.05) "${Math.round(v)}"
        else String.format("%.2f", v)
}
