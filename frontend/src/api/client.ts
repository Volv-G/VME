import type {
  AutoCutsResultDto,
  ClipDto,
  EventDto,
  FullRenderDto,
  MatchDto,
  MatchSummary,
  QueueStateDto,
  RenderFileDto,
  RenderJobDto,
  RosterDto,
  TeamSummary,
  TournamentInfoDto,
  TournamentSummary,
  YouTubeStatusDto,
} from "../types/api";

// import.meta.env.BASE_URL is "/" in dev and "/vme/" in production builds.
// API calls go to <BASE_URL>api/... so they sit alongside the SPA assets.
const BASE = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api`;

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

const enc = encodeURIComponent;

/** Encode a path segment-by-segment so `/` separators stay literal.
 *  `encodeURIComponent` would turn them into `%2F`, which FastAPI's
 *  `{path}` converter does NOT decode back to a slash - so download
 *  / delete on nested render files (`highlights/.../foo.mp4`) would 404.
 */
function encPath(p: string): string {
  return p.split("/").map(encodeURIComponent).join("/");
}
// All match-scoped routes share this prefix. Match identity is now
// (team, tournament, date, match), so the URL carries a `/dates/<date>/`
// segment between the tournament and the match.
const matchBase = (
  team: string,
  tournament: string,
  date: string,
  match: string
) =>
  `/teams/${enc(team)}/tournaments/${enc(tournament)}` +
  `/dates/${enc(date)}/matches/${enc(match)}`;

export const api = {
  // ---- Teams + roster ---------------------------------------------------
  async listTeams(): Promise<TeamSummary[]> {
    return fetchJson<TeamSummary[]>("/teams");
  },
  async createTeam(name: string): Promise<TeamSummary> {
    return fetchJson<TeamSummary>(`/teams?name=${enc(name)}`, { method: "POST" });
  },
  async getRoster(team: string): Promise<RosterDto> {
    return fetchJson<RosterDto>(`/teams/${enc(team)}/roster`);
  },
  async putRoster(team: string, roster: RosterDto): Promise<RosterDto> {
    return fetchJson<RosterDto>(`/teams/${enc(team)}/roster`, {
      method: "PUT",
      body: JSON.stringify(roster),
    });
  },

  // ---- Tournaments ------------------------------------------------------
  async listTournaments(team: string): Promise<TournamentSummary[]> {
    return fetchJson<TournamentSummary[]>(`/teams/${enc(team)}/tournaments`);
  },
  async createTournament(team: string, name: string): Promise<TournamentSummary> {
    return fetchJson<TournamentSummary>(`/teams/${enc(team)}/tournaments`, {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  },
  async deleteTournament(team: string, tournament: string): Promise<void> {
    await fetchJson<unknown>(`/teams/${enc(team)}/tournaments/${enc(tournament)}`, {
      method: "DELETE",
    });
  },
  async getTournamentInfo(
    team: string,
    tournament: string
  ): Promise<TournamentInfoDto> {
    return fetchJson<TournamentInfoDto>(
      `/teams/${enc(team)}/tournaments/${enc(tournament)}/info`
    );
  },
  async putTournamentInfo(
    team: string,
    tournament: string,
    info: TournamentInfoDto
  ): Promise<TournamentInfoDto> {
    return fetchJson<TournamentInfoDto>(
      `/teams/${enc(team)}/tournaments/${enc(tournament)}/info`,
      { method: "PUT", body: JSON.stringify(info) }
    );
  },

  // ---- Team-wide full renders + YouTube uploads -------------------------
  async listTeamFullRenders(team: string): Promise<FullRenderDto[]> {
    return fetchJson<FullRenderDto[]>(`/teams/${enc(team)}/full-renders`);
  },
  async youtubeStatus(): Promise<YouTubeStatusDto> {
    return fetchJson<YouTubeStatusDto>(`/youtube/status`);
  },
  async enqueueYouTubeUpload(
    team: string,
    tournament: string,
    date: string,
    match: string,
    body: {
      filename: string;
      titleOverride?: string;
      descriptionOverride?: string;
      privacyOverride?: string;
      playlistOverride?: string;
    }
  ): Promise<RenderJobDto> {
    return fetchJson<RenderJobDto>(
      `${matchBase(team, tournament, date, match)}/uploads/youtube`,
      {
        method: "POST",
        body: JSON.stringify({
          filename: body.filename,
          title_override: body.titleOverride ?? null,
          description_override: body.descriptionOverride ?? null,
          privacy_override: body.privacyOverride ?? null,
          playlist_override: body.playlistOverride ?? null,
        }),
      }
    );
  },

  // ---- Matches ----------------------------------------------------------
  async listMatches(team: string, tournament: string): Promise<MatchSummary[]> {
    return fetchJson<MatchSummary[]>(
      `/teams/${enc(team)}/tournaments/${enc(tournament)}/matches`
    );
  },
  async createMatch(
    team: string,
    tournament: string,
    payload: { opponent: string; date: string; match_index?: number | null }
  ): Promise<MatchDto> {
    return fetchJson<MatchDto>(
      `/teams/${enc(team)}/tournaments/${enc(tournament)}/matches`,
      { method: "POST", body: JSON.stringify(payload) }
    );
  },
  async getMatch(
    team: string,
    tournament: string,
    date: string,
    match: string
  ): Promise<MatchDto> {
    return fetchJson<MatchDto>(matchBase(team, tournament, date, match));
  },
  async patchMatch(
    team: string,
    tournament: string,
    date: string,
    match: string,
    body: Record<string, unknown>
  ): Promise<MatchDto> {
    return fetchJson<MatchDto>(matchBase(team, tournament, date, match), {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },
  async deleteMatch(
    team: string,
    tournament: string,
    date: string,
    match: string
  ): Promise<void> {
    await fetchJson<unknown>(matchBase(team, tournament, date, match), {
      method: "DELETE",
    });
  },

  // ---- Clips ------------------------------------------------------------
  /** Upload a clip file using the chunked / resumable protocol.
   *
   *  Why chunked: IIS/ARR use signed int32 byte counters internally and
   *  silently stall single requests larger than ~2 GiB even when
   *  maxAllowedContentLength is raised. Full-length match clips are
   *  routinely 2-3 GiB, so they have to be split into smaller PUTs.
   *  Each chunk request is well under the barrier and any one chunk
   *  failing can be retried (or the whole upload resumed by querying
   *  the session's `received` and slicing from there).
   *
   *  Protocol (matches backend `app/api/clips.py`):
   *    1. POST .../clips/uploads        -> {session_id, received: 0}
   *    2. PUT  .../clips/uploads/{sid}/chunk?offset=N   (repeat)
   *    3. POST .../clips/uploads/{sid}/finalize         -> MatchDto
   *  Cancel via DELETE .../clips/uploads/{sid}.
   *
   *  `signal` lets the caller abort mid-upload (we also DELETE the
   *  server session so the .part file is cleaned up). Progress is
   *  reported per chunk (~50 events for a 3 GiB clip at 32 MiB chunks)
   *  which is plenty smooth for a progress bar.
   */
  async uploadClip(
    team: string,
    tournament: string,
    date: string,
    match: string,
    file: File,
    onProgress?: (pct: number) => void,
    signal?: AbortSignal
  ): Promise<MatchDto> {
    const CHUNK_SIZE = 32 * 1024 * 1024; // 32 MiB - well under the 2 GiB IIS cap
    const base = `${BASE}${matchBase(team, tournament, date, match)}/clips/uploads`;

    // --- 1. open session
    const startRes = await fetch(base, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: file.name, total_size: file.size }),
      signal,
    });
    if (!startRes.ok) {
      throw new Error(`session create failed: ${startRes.status} ${await startRes.text()}`);
    }
    const session = (await startRes.json()) as {
      session_id: string;
      received: number;
    };
    const sid = session.session_id;

    // Wrap the rest so we DELETE the session on abort / failure to free
    // the server-side .part file. Avoids accumulating orphan tempfiles
    // when uploads are repeatedly cancelled.
    const cleanup = () => {
      // Best-effort; ignore failures (session might already be gone).
      fetch(`${base}/${sid}`, { method: "DELETE" }).catch(() => {});
    };
    const onAbort = () => cleanup();
    signal?.addEventListener("abort", onAbort);

    try {
      // --- 2. upload chunks sequentially
      let received = session.received;
      while (received < file.size) {
        const end = Math.min(received + CHUNK_SIZE, file.size);
        const chunk = file.slice(received, end);
        const putRes = await fetch(`${base}/${sid}/chunk?offset=${received}`, {
          method: "PUT",
          headers: { "Content-Type": "application/octet-stream" },
          body: chunk,
          signal,
        });
        if (!putRes.ok) {
          throw new Error(`chunk upload failed: ${putRes.status} ${await putRes.text()}`);
        }
        const state = (await putRes.json()) as { received: number };
        received = state.received;
        if (onProgress) onProgress((100 * received) / file.size);
      }

      // --- 3. finalize -> server probes, transcodes if needed, returns MatchDto
      const finRes = await fetch(`${base}/${sid}/finalize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal,
      });
      if (!finRes.ok) {
        throw new Error(`finalize failed: ${finRes.status} ${await finRes.text()}`);
      }
      return (await finRes.json()) as MatchDto;
    } catch (err) {
      // If the failure wasn't an abort, still try to clean up the
      // server session so it doesn't sit there until the TTL fires.
      if (!signal?.aborted) cleanup();
      throw err;
    } finally {
      signal?.removeEventListener("abort", onAbort);
    }
  },

  /** Server-side import: instruct the backend to copy/move a file or a
   *  directory of files from a local path on the server into the match
   *  folder. Skips HTTP body transfer entirely - best option when the
   *  browser and the server are on the same machine and the user knows
   *  where the source clips live on disk.
   */
  async importClipPath(
    team: string,
    tournament: string,
    date: string,
    match: string,
    sourcePath: string,
    mode: "copy" | "move" = "copy"
  ): Promise<{ imported: string[]; match: MatchDto }> {
    return fetchJson(
      `${matchBase(team, tournament, date, match)}/clips/import-path`,
      {
        method: "POST",
        body: JSON.stringify({ source_path: sourcePath, mode }),
      }
    );
  },
  async reorderClips(
    team: string,
    tournament: string,
    date: string,
    match: string,
    clipIds: string[]
  ): Promise<MatchDto> {
    return fetchJson<MatchDto>(
      `${matchBase(team, tournament, date, match)}/clips`,
      { method: "PUT", body: JSON.stringify({ clip_ids: clipIds }) }
    );
  },
  async deleteClip(
    team: string,
    tournament: string,
    date: string,
    match: string,
    clipId: string
  ): Promise<MatchDto> {
    return fetchJson<MatchDto>(
      `${matchBase(team, tournament, date, match)}/clips/${clipId}`,
      { method: "DELETE" }
    );
  },
  async autoCuts(
    team: string,
    tournament: string,
    date: string,
    match: string,
    opts: { hasIntroClip?: boolean } = {}
  ): Promise<AutoCutsResultDto> {
    return fetchJson<AutoCutsResultDto>(
      `${matchBase(team, tournament, date, match)}/clips/auto-cuts`,
      {
        method: "POST",
        body: JSON.stringify({ has_intro_clip: !!opts.hasIntroClip }),
      }
    );
  },

  // ---- Events -----------------------------------------------------------
  async createEvent(
    team: string,
    tournament: string,
    date: string,
    match: string,
    body: { type: string; clip_id?: string; local_frame?: number; payload?: Record<string, unknown> }
  ): Promise<MatchDto> {
    return fetchJson<MatchDto>(
      `${matchBase(team, tournament, date, match)}/events`,
      { method: "POST", body: JSON.stringify(body) }
    );
  },
  async patchEvent(
    team: string,
    tournament: string,
    date: string,
    match: string,
    eventId: number,
    body: { clip_id?: string; local_frame?: number; payload?: Record<string, unknown> }
  ): Promise<MatchDto> {
    return fetchJson<MatchDto>(
      `${matchBase(team, tournament, date, match)}/events/${eventId}`,
      { method: "PATCH", body: JSON.stringify(body) }
    );
  },
  async deleteEvent(
    team: string,
    tournament: string,
    date: string,
    match: string,
    eventId: number
  ): Promise<MatchDto> {
    return fetchJson<MatchDto>(
      `${matchBase(team, tournament, date, match)}/events/${eventId}`,
      { method: "DELETE" }
    );
  },

  // ---- Render queue -----------------------------------------------------
  /**
   * Enqueue a render job. Returns the new job in `pending` status.
   *
   * The job does NOT start until the global queue is active
   * (`startQueue()`); this lets the user line up several renders without
   * the server tying up CPU mid-edit.
   */
  async enqueueRender(
    team: string,
    tournament: string,
    date: string,
    match: string,
    opts: {
      label?: string;
      /** Render kind. Defaults to "full" server-side. */
      kind?: "full" | "preview" | "highlights" | "focused_highlights";
      /** Source/global frame to center a preview around (omit for full render). */
      playheadFrame?: number;
      /** Half-window length in seconds for preview (defaults to 30 on the server). */
      secondsAround?: number;
      /** When true, bypass the queue and start rendering immediately. */
      immediate?: boolean;
    } = {}
  ): Promise<RenderJobDto> {
    return fetchJson<RenderJobDto>(
      `${matchBase(team, tournament, date, match)}/renders`,
      {
        method: "POST",
        body: JSON.stringify({
          label: opts.label,
          kind: opts.kind,
          playhead_frame: opts.playheadFrame,
          seconds_around: opts.secondsAround,
          immediate: opts.immediate,
        }),
      }
    );
  },
  async cancelRender(jobId: string): Promise<RenderJobDto> {
    return fetchJson<RenderJobDto>(`/jobs/${jobId}/cancel`, { method: "POST" });
  },
  async deleteJob(jobId: string): Promise<void> {
    await fetchJson<unknown>(`/jobs/${jobId}`, { method: "DELETE" });
  },
  async listTeamJobs(team: string): Promise<RenderJobDto[]> {
    return fetchJson<RenderJobDto[]>(`/teams/${enc(team)}/jobs`);
  },
  async getQueueState(): Promise<QueueStateDto> {
    return fetchJson<QueueStateDto>(`/queue/state`);
  },
  async startQueue(): Promise<QueueStateDto> {
    return fetchJson<QueueStateDto>(`/queue/start`, { method: "POST" });
  },
  async stopQueue(): Promise<QueueStateDto> {
    return fetchJson<QueueStateDto>(`/queue/stop`, { method: "POST" });
  },
  jobEventsUrl(jobId: string): string {
    return `${BASE}/jobs/${jobId}/events`;
  },
  async listRenders(
    team: string,
    tournament: string,
    date: string,
    match: string
  ): Promise<RenderFileDto[]> {
    return fetchJson<RenderFileDto[]>(
      `${matchBase(team, tournament, date, match)}/renders`
    );
  },
  async deleteRender(
    team: string,
    tournament: string,
    date: string,
    match: string,
    filename: string
  ): Promise<void> {
    await fetchJson<unknown>(
      `${matchBase(team, tournament, date, match)}/renders/${encPath(filename)}`,
      { method: "DELETE" }
    );
  },
  downloadUrl(
    team: string,
    tournament: string,
    date: string,
    match: string,
    filename: string
  ): string {
    return `${BASE}${matchBase(team, tournament, date, match)}/renders/${encPath(filename)}`;
  },
  clipStreamUrl(
    team: string,
    tournament: string,
    date: string,
    match: string,
    clipId: string
  ): string {
    return `${BASE}${matchBase(team, tournament, date, match)}/media/clips/${clipId}/stream`;
  },
};

export type {
  AutoCutsResultDto,
  ClipDto,
  RenderFileDto,
  EventDto,
  MatchDto,
  MatchSummary,
  QueueStateDto,
  RenderJobDto,
  RosterDto,
  TeamSummary,
  TournamentSummary,
};
