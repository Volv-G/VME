package works.vme.streamer.data

/**
 * Where the picture comes from.
 *
 * [Hdmi] is the normal setup: a DSLR or camcorder on a tripod, its
 * HDMI out through a USB capture adapter into the phone, which is then
 * free to be held and used for scoring. [PhoneCamera] is for when
 * there is no camera to plug in - a flat battery, a forgotten cable,
 * an away game travelled light - and the phone itself goes on the
 * tripod, in landscape, and films. Its sound then comes from the
 * phone's microphone, since there is no adapter to carry any.
 */
enum class VideoInput(val key: String, val label: String, val chip: String) {
    Hdmi("hdmi", "HDMI adapter (DSLR)", "HDMI"),
    PhoneCamera("phone", "Phone camera", "Phone cam");

    companion object {
        val DEFAULT = Hdmi

        fun fromKey(key: String?): VideoInput =
            values().firstOrNull { it.key == key } ?: DEFAULT
    }
}
