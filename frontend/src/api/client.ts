import type {
  AutoCutsResultDto,
  ClipDto,
  EventDto,
  MatchDto,
  MatchSummary,
  RenderJobDto,
  RosterDto,
  TeamSummary,
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

export const api = {
  async listTeams(): Promise<TeamSummary[]> {
    return fetchJson<TeamSummary[]>("/teams");
  },
  async createTeam(name: string): Promise<TeamSummary> {
    return fetchJson<TeamSummary>(`/teams?name=${encodeURIComponent(name)}`, { method: "POST" });
  },
  async getRoster(team: string): Promise<RosterDto> {
    return fetchJson<RosterDto>(`/teams/${encodeURIComponent(team)}/roster`);
  },
  async putRoster(team: string, roster: RosterDto): Promise<RosterDto> {
    return fetchJson<RosterDto>(`/teams/${encodeURIComponent(team)}/roster`, {
      method: "PUT",
      body: JSON.stringify(roster),
    });
  },

  async listMatches(team: string): Promise<MatchSummary[]> {
    return fetchJson<MatchSummary[]>(`/teams/${encodeURIComponent(team)}/matches`);
  },
  async createMatch(team: string, payload: { name: string; opponent?: string; date?: string }): Promise<MatchDto> {
    return fetchJson<MatchDto>(`/teams/${encodeURIComponent(team)}/matches`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  async getMatch(team: string, match: string): Promise<MatchDto> {
    return fetchJson<MatchDto>(`/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(match)}`);
  },
  async patchMatch(team: string, match: string, body: Record<string, unknown>): Promise<MatchDto> {
    return fetchJson<MatchDto>(`/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(match)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },
  async deleteMatch(team: string, match: string): Promise<void> {
    await fetchJson<unknown>(`/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(match)}`, { method: "DELETE" });
  },
  async rescanMatch(team: string, match: string): Promise<MatchDto> {
    return fetchJson<MatchDto>(`/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(match)}/rescan`, { method: "POST" });
  },

  async uploadClip(team: string, match: string, file: File, onProgress?: (pct: number) => void): Promise<MatchDto> {
    const url = `${BASE}/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(match)}/clips`;
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
  async reorderClips(team: string, match: string, clipIds: string[]): Promise<MatchDto> {
    return fetchJson<MatchDto>(`/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(match)}/clips`, {
      method: "PUT",
      body: JSON.stringify({ clip_ids: clipIds }),
    });
  },
  async deleteClip(team: string, match: string, clipId: string): Promise<MatchDto> {
    return fetchJson<MatchDto>(`/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(match)}/clips/${clipId}`, {
      method: "DELETE",
    });
  },
  async autoCuts(team: string, match: string): Promise<AutoCutsResultDto> {
    return fetchJson<AutoCutsResultDto>(
      `/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(match)}/clips/auto-cuts`,
      { method: "POST" }
    );
  },

  async createEvent(team: string, match: string, body: { type: string; clip_id?: string; local_frame?: number; payload?: Record<string, unknown> }): Promise<MatchDto> {
    return fetchJson<MatchDto>(`/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(match)}/events`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  async patchEvent(team: string, match: string, eventId: number, body: { clip_id?: string; local_frame?: number; payload?: Record<string, unknown> }): Promise<MatchDto> {
    return fetchJson<MatchDto>(`/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(match)}/events/${eventId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },
  async deleteEvent(team: string, match: string, eventId: number): Promise<MatchDto> {
    return fetchJson<MatchDto>(`/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(match)}/events/${eventId}`, {
      method: "DELETE",
    });
  },

  async startRender(
    team: string,
    match: string,
    opts: {
      label?: string;
      /** Source/global frame to center a preview around (omit for full render). */
      playheadFrame?: number;
      /** Half-window length in seconds for preview (defaults to 30 on the server). */
      secondsAround?: number;
    } = {}
  ): Promise<RenderJobDto> {
    return fetchJson<RenderJobDto>(`/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(match)}/renders`, {
      method: "POST",
      body: JSON.stringify({
        label: opts.label,
        playhead_frame: opts.playheadFrame,
        seconds_around: opts.secondsAround,
      }),
    });
  },
  async cancelRender(jobId: string): Promise<RenderJobDto> {
    return fetchJson<RenderJobDto>(`/jobs/${jobId}/cancel`, { method: "POST" });
  },
  jobEventsUrl(jobId: string): string {
    return `${BASE}/jobs/${jobId}/events`;
  },
  downloadUrl(team: string, match: string, filename: string): string {
    return `${BASE}/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(match)}/renders/${encodeURIComponent(filename)}`;
  },
  clipStreamUrl(team: string, match: string, clipId: string): string {
    return `${BASE}/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(match)}/media/clips/${clipId}/stream`;
  },
};

export type { AutoCutsResultDto, ClipDto, EventDto, MatchDto, MatchSummary, RenderJobDto, RosterDto, TeamSummary };
