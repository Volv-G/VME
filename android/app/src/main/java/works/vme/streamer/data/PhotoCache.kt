package works.vme.streamer.data

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import java.io.File

/**
 * On-disk cache of player profile pictures, fetched once at roster
 * import and read from local storage thereafter.
 *
 * ### Why fetch at import and not on demand
 *
 * The pop-up renderer runs on the scoring path -- the operator taps
 * "Kill", we draw a bitmap, we hand it to GL. Doing network I/O there
 * would put a gym Wi-Fi round trip between the tap and the overlay,
 * and a slow or flaky one would stall the UI thread. Roster import is
 * already a network operation the operator waits on in the pre-match
 * warm-up, so that is where the photos come from too. A twelve-player
 * roster is twelve small GETs, once.
 *
 * ### Layout
 *
 * ```
 * filesDir/photos/{teamSlug}/{jersey}.img
 * ```
 *
 * The extension is deliberately generic: VME serves whatever was
 * uploaded (JPEG, PNG, WebP) and `BitmapFactory` sniffs the format
 * from the bytes, so recording it buys nothing.
 *
 * Jersey number is the filename because that is what VME keys photos
 * by (`paths.player_photo_stem(number)`) -- renaming a player keeps
 * their photo, and the pop-up looks a player up by number anyway.
 *
 * ### Downscaling
 *
 * Photos are stored as uploaded, which may be several megapixels. The
 * pop-up avatar is at most ~80 px across on a 1080p frame, so [load]
 * decodes with an `inSampleSize` sized to the request rather than
 * pulling a 12 MP bitmap into memory to draw a thumbnail.
 */
object PhotoCache {

    /** Root of the cache for one team. */
    fun dirFor(context: Context, teamSlug: String): File =
        File(File(context.filesDir, "photos"), teamSlug)

    /** Where a given player's photo lives, whether or not it exists. */
    fun fileFor(context: Context, teamSlug: String, jersey: Int): File =
        File(dirFor(context, teamSlug), "$jersey.img")

    /**
     * Cache key for a match's opponent.
     *
     * Opponent logos are per match, not per team: the opponent has no
     * roster on the server to hang one off, and the same club can
     * turn up in several matches with a badge the operator grabbed
     * separately each time. The prefix cannot collide with a team
     * slug because VME slugs never start with an underscore.
     */
    fun opponentKey(matchSlug: String): String = "_opp_$matchSlug"

    /**
     * Where a team's logo lives. Beside the player photos, under a
     * name no jersey number can produce.
     */
    fun logoFileFor(context: Context, key: String): File =
        File(dirFor(context, key), "team-logo.img")

    /** Write [bytes] as the logo for [key]. Returns the absolute
     *  path, or null if the write failed. */
    fun storeLogo(context: Context, key: String, bytes: ByteArray): String? =
        runCatching {
            val dir = dirFor(context, key)
            dir.mkdirs()
            val f = File(dir, "team-logo.img")
            f.writeBytes(bytes)
            f.absolutePath
        }.getOrNull()

    /**
     * Write [bytes] as the photo for [jersey]. Returns the absolute
     * path, or null if the write failed (a full disk must not fail
     * the whole import).
     */
    fun store(
        context: Context,
        teamSlug: String,
        jersey: Int,
        bytes: ByteArray,
    ): String? = runCatching {
        val dir = dirFor(context, teamSlug)
        dir.mkdirs()
        val f = File(dir, "$jersey.img")
        f.writeBytes(bytes)
        f.absolutePath
    }.getOrNull()

    /**
     * Decode a cached photo at roughly [targetPx] on its short side.
     * Returns null when the path is null, missing, or not decodable
     * -- callers fall back to a jersey-number disc.
     */
    fun load(path: String?, targetPx: Int): Bitmap? {
        if (path.isNullOrBlank()) return null
        val f = File(path)
        if (!f.exists()) return null
        return runCatching {
            // Pass 1: dimensions only.
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(path, bounds)
            val short = minOf(bounds.outWidth, bounds.outHeight)
            var sample = 1
            while (short / (sample * 2) >= targetPx && sample < 32) sample *= 2
            // Pass 2: the real decode, downscaled.
            BitmapFactory.decodeFile(
                path,
                BitmapFactory.Options().apply { inSampleSize = sample },
            )
        }.getOrNull()
    }

    /** Delete every cached photo for a team. Used when a re-import
     *  should not leave a departed player's face behind. */
    fun clear(context: Context, teamSlug: String) {
        runCatching { dirFor(context, teamSlug).deleteRecursively() }
    }
}
