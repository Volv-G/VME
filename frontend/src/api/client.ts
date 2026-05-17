import type {
  AutoCutsResultDto,
  ClipDto,
  EventDto,
  MatchDto,
  MatchSummary,
  RenderFileDto,
  RenderJobDto,
  RosterDto,
  TeamSummary,
  TournamentSummary,
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
  async rescanMatch(
    team: string,
    tournament: string,
    date: string,
    match: string
  ): Promise<MatchDto> {
    return fetchJson<MatchDto>(
      `${matchBase(team, tournament, date, match)}/rescan`,
      { method: "POST" }
    );
  },

  // ---- Clips ------------------------------------------------------------
  async uploadClip(
    team: string,
    tournament: string,
    date: string,
    match: string,
    file: File,
    onProgress?: (pct: number) => void
  ): Promise<MatchDto> {
    const url = `${BASE}${matchBase(team, tournament, date, match)}/clips`;
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress((100 * e.loaded) / e.total);
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText));
        else reject(new Error(xhr.responseText || xhr.statusText));
      };
      xhr.onerror = () => reject(new Error("Upload failed"));
      const fd = new FormData();
      fd.append("file", file);
      xhr.send(fd);
    });
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
    match: string
  ): Promise<AutoCutsResultDto> {
    return fetchJson<AutoCutsResultDto>(
      `${matchBase(team, tournament, date, match)}/clips/auto-cuts`,
      { method: "POST" }
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

  // ---- Renders ----------------------------------------------------------
  async startRender(
    team: string,
    tournament: string,
    date: string,
    match: string,
    opts: {
      label?: string;
      /** Source/global frame to center a preview around (omit for full render). */
      playheadFrame?: number;
      /** Half-window length in seconds for preview (defaults to 30 on the server). */
      secondsAround?: number;
    } = {}
  ): Promise<RenderJobDto> {
    return fetchJson<RenderJobDto>(
      `${matchBase(team, tournament, date, match)}/renders`,
      {
        method: "POST",
        body: JSON.stringify({
          label: opts.label,
          playhead_frame: opts.playheadFrame,
          seconds_around: opts.secondsAround,
        }),
      }
    );
  },
  async cancelRender(jobId: string): Promise<RenderJobDto> {
    return fetchJson<RenderJobDto>(`/jobs/${jobId}/cancel`, { method: "POST" });
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
      `${matchBase(team, tournament, date, match)}/renders/${enc(filename)}`,
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
    return `${BASE}${matchBase(team, tournament, date, match)}/renders/${enc(filename)}`;
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
  RenderJobDto,
  RosterDto,
  TeamSummary,
  TournamentSummary,
};
