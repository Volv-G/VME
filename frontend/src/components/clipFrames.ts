import type { ClipDto } from "../types/api";

/**
 * Map a global timeline frame onto the clip that contains it.
 *
 * Events are stored as `clip_id` + `local_frame`, but everything the user
 * interacts with (playhead, timeline, cut spans) is expressed in global
 * frames, so every "create an event here" path needs this conversion.
 *
 * A frame past the end of the last clip clamps to that clip's final frame
 * rather than returning null: the playhead can legitimately sit at the very
 * end of the timeline, and refusing to log an event there would be worse
 * than logging it one frame early. Returns null only when there are no
 * clips at all.
 */
export function resolveClipFromGlobal(
  clips: ClipDto[],
  globalFrame: number
): { clipId: string; localFrame: number } | null {
  let acc = 0;
  for (const c of clips) {
    if (globalFrame < acc + c.frame_count) {
      return { clipId: c.id, localFrame: globalFrame - acc };
    }
    acc += c.frame_count;
  }
  if (clips.length === 0) return null;
  const last = clips[clips.length - 1];
  return { clipId: last.id, localFrame: last.frame_count - 1 };
}
