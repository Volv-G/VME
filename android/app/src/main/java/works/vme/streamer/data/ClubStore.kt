package works.vme.streamer.data

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * The opponents' clubs and schools set up in VME - name, colours, badge -
 * kept on the phone so they can be picked at the gym, where VME may be
 * out of reach. Refreshed from VME whenever a picker opens and VME
 * answers; the last list is used otherwise.
 *
 *   files/clubs/clubs.json    [{id, name, colors}]
 *   files/clubs/<id>.img      the badge, when the club has one
 */
object ClubStore {

    data class Club(val id: String, val name: String, val colors: List<String>, val logo: File?) {
        /** The main colour: the first. */
        val color: String? get() = colors.firstOrNull()
    }

    private fun dir(ctx: Context) = File(ctx.filesDir, "clubs")
    private fun list(ctx: Context) = File(dir(ctx), "clubs.json")
    private fun logo(ctx: Context, id: String) = File(dir(ctx), "${id.replace(Regex("[^a-z0-9-]"), "_")}.img")

    /** The clubs from the last refresh, by name; empty before the first. */
    fun cached(ctx: Context): List<Club> = runCatching {
        val arr = JSONArray(list(ctx).readText())
        (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            val id = o.getString("id")
            val colors = o.optJSONArray("colors")
            Club(
                id = id,
                name = o.optString("name"),
                colors = (0 until (colors?.length() ?: 0)).map { colors!!.getString(it) },
                logo = logo(ctx, id).takeIf { it.isFile },
            )
        }
    }.getOrDefault(emptyList())

    /** Fetch the list and badges from VME and keep them. Blocking; throws
     *  when VME can't be reached (the cached list stays as it was). */
    fun refresh(ctx: Context): List<Club> {
        val base = Settings.baseUrl(ctx)
        if (base.isBlank()) return cached(ctx)
        val creds = Settings.credentials(ctx)
        val clubs = VmeClient.listClubs(base, creds)
        dir(ctx).mkdirs()
        val keep = mutableSetOf(list(ctx).name)
        for (c in clubs) {
            val f = logo(ctx, c.id)
            if (c.hasLogo) {
                VmeClient.fetchClubLogo(base, c.id, creds)?.let { f.writeBytes(it) }
                keep += f.name
            }
        }
        val arr = JSONArray()
        clubs.forEach { c -> arr.put(JSONObject().put("id", c.id).put("name", c.name).put("colors", JSONArray(c.colors))) }
        val tmp = File(dir(ctx), "clubs.json.tmp")
        tmp.writeText(arr.toString())
        tmp.renameTo(list(ctx))
        // Badges of clubs gone or no longer with one.
        dir(ctx).listFiles()?.filter { it.name !in keep && it.name != tmp.name }?.forEach { it.delete() }
        return cached(ctx)
    }
}
