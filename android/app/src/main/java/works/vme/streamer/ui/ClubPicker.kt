package works.vme.streamer.ui

import android.app.Activity
import android.app.AlertDialog
import android.graphics.BitmapFactory
import android.graphics.Color
import android.text.SpannableString
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import works.vme.streamer.data.ClubStore
import works.vme.streamer.data.Settings

/**
 * Pick one of the clubs set up in VME, then which of its colours this
 * team of theirs wears (asked only when the club has more than one).
 *
 * Shows the list kept on the phone straight away and refreshes it from
 * VME meanwhile; with nothing kept yet, it waits for VME.
 */
object ClubPicker {

    fun pick(activity: Activity, onPicked: (ClubStore.Club, String?) -> Unit) {
        val cached = ClubStore.cached(activity)
        if (cached.isNotEmpty()) showClubs(activity, cached, onPicked)
        if (!Settings.isConfigured(activity)) {
            if (cached.isEmpty()) toast(activity, "Set the VME address in Settings to get the clubs.")
            return
        }
        if (cached.isEmpty()) toast(activity, "Getting the clubs from VME...")
        Thread {
            val fresh = runCatching { ClubStore.refresh(activity.applicationContext) }
            if (cached.isNotEmpty()) return@Thread
            activity.runOnUiThread {
                if (activity.isFinishing || activity.isDestroyed) return@runOnUiThread
                fresh.onSuccess {
                    if (it.isEmpty()) toast(activity, "No clubs in VME yet - add them on its Clubs page.")
                    else showClubs(activity, it, onPicked)
                }.onFailure { toast(activity, "Could not reach VME: ${it.message}") }
            }
        }.start()
    }

    private fun showClubs(activity: Activity, clubs: List<ClubStore.Club>, onPicked: (ClubStore.Club, String?) -> Unit) {
        val d = activity.resources.displayMetrics.density
        val adapter = object : BaseAdapter() {
            override fun getCount() = clubs.size
            override fun getItem(i: Int) = clubs[i]
            override fun getItemId(i: Int) = i.toLong()
            override fun getView(i: Int, convertView: View?, parent: ViewGroup?): View {
                val c = clubs[i]
                return LinearLayout(activity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                    setPadding((20 * d).toInt(), (10 * d).toInt(), (20 * d).toInt(), (10 * d).toInt())
                    val size = (32 * d).toInt()
                    val bitmap = c.logo?.let { runCatching { BitmapFactory.decodeFile(it.absolutePath) }.getOrNull() }
                    addView(if (bitmap != null) ImageView(activity).apply { setImageBitmap(bitmap) } else View(activity).apply {
                        setBackgroundColor(c.color?.let { runCatching { Color.parseColor(it) }.getOrNull() } ?: Color.DKGRAY)
                    }, LinearLayout.LayoutParams(size, size).apply { rightMargin = (14 * d).toInt() })
                    addView(TextView(activity).apply {
                        text = c.name
                        textSize = 16f
                    })
                }
            }
        }
        AlertDialog.Builder(activity)
            .setTitle("Club")
            .setAdapter(adapter) { _, i -> pickColor(activity, clubs[i], onPicked) }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun pickColor(activity: Activity, club: ClubStore.Club, onPicked: (ClubStore.Club, String?) -> Unit) {
        if (club.colors.size <= 1) {
            onPicked(club, club.color)
            return
        }
        val labels = club.colors.map { hex ->
            SpannableString("■■  $hex").apply {
                runCatching { Color.parseColor(hex) }.getOrNull()?.let {
                    setSpan(ForegroundColorSpan(it), 0, 2, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                }
            }
        }.toTypedArray<CharSequence>()
        AlertDialog.Builder(activity)
            .setTitle("${club.name}: colour")
            .setItems(labels) { _, i -> onPicked(club, club.colors[i]) }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun toast(activity: Activity, msg: String) =
        Toast.makeText(activity, msg, Toast.LENGTH_LONG).show()
}
