import { Link, Navigate, Route, Routes, useParams } from "react-router-dom";
import type { MatchDoc, MatchSummary, TeamDoc, TeamsDoc, Video } from "../shared/docs";
import { useEffect, useState, type ReactNode } from "react";
import type { Me } from "../shared/api";
import { formatDate, imageUrl, MeContext, onRefresh, savedInvites, startAutoRefresh, useApi, useDoc, useMe } from "./api";
import { Crest, Gate, InviteButton, matchPath, playerPath, teamPath, TeamTheme } from "./common";
import { AdminPage } from "./Admin";
import { JoinPage } from "./Join";
import { PlayerEditor } from "./PlayerEditor";
import { StatsTable } from "./StatsTable";
import { VideoTile, videoKey } from "./YouTube";
import { knownId, loadReveals, matchSpoiler, seasonSpoiler, seenId, Spoiler, useRevealed, useReveals } from "./Spoilers";
import { LiveBanner, LiveChip, UpcomingSection } from "./Schedule";

export function App() {
  const me = useApi<Me>("/api/me");
  const value = { me: me.state === "ok" ? me.doc : null, reload: me.reload };
  useEffect(() => {
    void loadReveals();
    // Reveals made on another device come over with each refresh too.
    const off = onRefresh(() => void loadReveals());
    const stop = startAutoRefresh();
    return () => {
      off();
      stop();
    };
  }, []);
  return (
    <MeContext.Provider value={value}>
      <TopBar />
      <div className="page">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/join/:code" element={<JoinPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/t/:team" element={<TeamPage />} />
          <Route path="/t/:team/p/:number" element={<PlayerPage />} />
          <Route path="/t/:team/m/:tournament/:date/:match" element={<MatchPage />} />
          <Route path="*" element={<p className="muted">Nothing here. <Link to="/">Home</Link></p>} />
        </Routes>
      </div>
    </MeContext.Provider>
  );
}

function TopBar() {
  const { me } = useMe();
  return (
    <nav className="topbar">
      <Link to="/" className="brand">Courtside VB</Link>
      <span className="topbar-right">
        {me?.admin && (
          <Link to="/admin">
            Admin{me.pending > 0 && <span className="badge" title="Requests waiting">{me.pending}</span>}
          </Link>
        )}
        {me && (
          <span className="muted small who" title={me.email}>
            {me.email}
          </span>
        )}
        {me && (
          <a href="/cdn-cgi/access/logout" className="small">
            Sign out
          </a>
        )}
      </span>
    </nav>
  );
}

function Home() {
  const { me } = useMe();
  const res = useDoc<TeamsDoc>("teams");
  const invites = savedInvites.all();
  const waiting = (me?.memberships ?? []).filter((m) => m.status !== "approved");
  const teams = res.state === "ok" ? res.doc.teams : null;
  // A parent or coach of one team has nothing to choose here: straight
  // to the team. (Not for the admin, who needs the list.)
  if (me && !me.admin && teams?.length === 1 && waiting.length === 0) {
    return <Navigate to={teamPath(teams[0].slug)} replace />;
  }
  // Teams whose invite this device opened but where nothing is approved
  // yet, or where another player could be claimed.
  const askMore = Object.entries(invites).filter(([team]) => teams?.some((t) => t.slug === team));
  return (
    <>
      <header className="masthead">
        <h1>Matches, highlights and stats</h1>
      </header>
      {waiting.length > 0 && (
        <div className="list notices">
          {waiting.map((m) => (
            <div key={m.id} className="row">
              <span className="row-main">
                {m.role === "coach" ? "Coach" : m.player_name} · {m.team_name}
              </span>
              <span className={`chip chip-${m.status}`}>
                {m.status === "pending" ? "waiting for approval" : "declined"}
              </span>
            </div>
          ))}
        </div>
      )}
      <Gate res={res}>
        {(doc) =>
          doc.teams.length ? (
            <div className="cards">
              {doc.teams.map((t) => (
                <Link key={t.slug} to={teamPath(t.slug)} className="card team-card" style={{ borderTopColor: t.color }}>
                  <Crest src={t.logo} color={t.color} label={t.name} size={56} />
                  <strong>{t.name}</strong>
                </Link>
              ))}
            </div>
          ) : waiting.length ? null : (
            <p className="notice">
              This site is for team families. To get in, open the invite link your team shared.
              {Object.values(invites).length > 0 && (
                <>
                  {" "}
                  <Link to={`/join/${Object.values(invites)[0]}`}>Open the one you used before</Link>
                </>
              )}
            </p>
          )
        }
      </Gate>
      {askMore.length > 0 && (
        <p className="small">
          {askMore.map(([team, code]) => (
            <Link key={team} to={`/join/${code}`} className="ask-more">
              Ask for access to another {teams?.find((t) => t.slug === team)?.name} player
            </Link>
          ))}
        </p>
      )}
    </>
  );
}

function Score({ m }: { m: MatchSummary }) {
  return (
    <span className="score">
      {m.result && <span className={`result result-${m.result}`}>{m.result}</span>}
      <strong>
        {m.home_sets}–{m.away_sets}
      </strong>
      {m.sets.length > 0 && <span className="muted sets">{m.sets.map(([h, a]) => `${h}-${a}`).join(", ")}</span>}
      {m.current && (
        <span className="now-score" title="This set, live from the court">
          now {m.current[0]}-{m.current[1]}
        </span>
      )}
      {m.score_source === "phone" && !m.current && (
        <span className="muted small" title="Kept on the phone at the match; VME's own replaces it once the video is tagged">
          (courtside)
        </span>
      )}
    </span>
  );
}

type Revealed = (id: string) => boolean;

/** Whether this person has opened the match page. (Before matches were
 *  marked, opening one marked its videos known; that counts too.) */
function matchSeen(team: string, m: MatchSummary, revealed: Revealed): boolean {
  return revealed(seenId(team, m.key)) || m.videos.some((v) => revealed(knownId(team, m.key, videoKey(v))));
}

/** Videos added since this person last opened the match; none for a
 *  match they have never opened (all of it is new then). */
function newVideos(team: string, m: MatchSummary, revealed: Revealed): Video[] {
  if (!matchSeen(team, m, revealed)) return [];
  return m.videos.filter((v) => !revealed(knownId(team, m.key, videoKey(v))));
}

/** Two yes/no marks per match, both cleared by opening it: seen or not,
 *  and whether videos came since. */
function SeenChips({ team, m, revealed }: { team: string; m: MatchSummary; revealed: Revealed }) {
  const seen = matchSeen(team, m, revealed);
  return (
    <span className="seen-chips">
      <span className={`seen-chip${seen ? " is-seen" : ""}`}>{seen ? "✓ Seen" : "Not seen"}</span>
      {newVideos(team, m, revealed).length > 0 && <span className="new-chip">New videos</span>}
    </span>
  );
}

function SeasonSpoiler({ t, children }: { t: TeamDoc; children: ReactNode }) {
  const [revealed, reveal] = useRevealed(seasonSpoiler(t));
  return (
    <Spoiler revealed={revealed} onReveal={reveal} label="Show season stats" note="They include the latest matches.">
      {children}
    </Spoiler>
  );
}

function TeamPage() {
  const { team = "" } = useParams();
  const { me } = useMe();
  const res = useDoc<TeamDoc>(`team/${team}`);
  const spoilers = useReveals();
  const manyTeams =
    !me || me.admin || new Set(me.memberships.filter((m) => m.status === "approved").map((m) => m.team)).size > 1;
  return (
    <Gate res={res}>
      {(t) => {
        const hidden = [
          ...t.tournaments.flatMap((tour) => tour.matches.map((m) => matchSpoiler(t.slug, m.key))),
          seasonSpoiler(t),
        ].filter((id) => !spoilers.revealed(id));
        return (
          <>
            <TeamTheme color={t.color} logo={t.watermark ?? t.logo} icon={t.logo} />
            <header className="team-head">
              {manyTeams && <Link to="/" className="muted back">← Teams</Link>}
              <div className="team-title">
                <Crest src={t.logo} color={t.color} label={t.name} size={72} />
                <h1>{t.name}</h1>
                <InviteButton team={t.slug} teamName={t.name} />
              </div>
            </header>
            <LiveBanner streams={t.live ?? []} teamName={t.name} />

            <UpcomingSection t={t} onChanged={res.reload} />

            <section>
              <h2>
                Matches
                {hidden.length > 0 && (
                  <button type="button" className="reveal reveal-all" onClick={() => spoilers.reveal(...hidden)}>
                    👁 Reveal all scores
                  </button>
                )}
              </h2>
              {t.tournaments.length === 0 && <p className="muted small">No matches yet.</p>}
              {t.tournaments.map((tour) => (
                <div key={tour.slug} className="tournament">
                  <h3>{tour.name}</h3>
                  <div className="list">
                    {[...tour.matches].reverse().map((m) => {
                      const id = matchSpoiler(t.slug, m.key);
                      const stream = t.live?.find((s) => s.match_key === m.key);
                      return (
                        <div key={m.key} className="row row-link">
                          <Crest src={m.opponent_logo} label={m.opponent} size={32} />
                          <Link to={matchPath(t.slug, m.key)} className="row-main stretched">
                            <span>vs {m.opponent}</span>
                            <span className="muted small">
                              {formatDate(m.date)}
                              {m.index ? ` · Match ${m.index}` : ""}
                              {m.videos.length ? ` · ${m.videos.length} video${m.videos.length === 1 ? "" : "s"}` : ""}
                            </span>
                            <SeenChips team={t.slug} m={m} revealed={spoilers.revealed} />
                          </Link>
                          {stream && <LiveChip stream={stream} />}
                          {spoilers.revealed(id) ? (
                            <Score m={m} />
                          ) : (
                            <button type="button" className="reveal reveal-row" onClick={() => spoilers.reveal(id)}>
                              👁 Reveal score
                            </button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </section>

            {t.stats.players.length > 0 && (
              <section>
                <h2>Season stats</h2>
                <SeasonSpoiler t={t}>
                  <p className="muted small">{t.stats.matches} matches. Tap a column to sort.</p>
                  <StatsTable stats={t.stats} />
                </SeasonSpoiler>
              </section>
            )}

            <section>
              <h2>Players</h2>
              <div className="players">
                {t.players.map((p) => (
                  <Link key={p.number} to={playerPath(t.slug, p.number)} className="player">
                    {p.photo ? (
                      <img src={imageUrl(p.photo)} alt="" loading="lazy" />
                    ) : (
                      <span className="player-blank" style={{ background: t.color }}>#{p.number}</span>
                    )}
                    <span>
                      <strong>#{p.number}</strong> {p.name}
                    </span>
                  </Link>
                ))}
              </div>
            </section>
          </>
        );
      }}
    </Gate>
  );
}

function PlayerPage() {
  const { team = "", number = "" } = useParams();
  const { me } = useMe();
  const res = useDoc<TeamDoc>(`team/${team}`);
  const n = Number(number);
  const canEdit =
    !!me &&
    (me.admin ||
      me.memberships.some((m) => m.team === team && m.status === "approved" && m.role === "player" && m.number === n));
  return (
    <Gate res={res}>
      {(t) => {
        const p = t.players.find((x) => x.number === n);
        const line = t.stats.players.find((x) => x.number === n);
        const reels: { m: MatchSummary; v: Video }[] = [];
        for (const tour of t.tournaments)
          for (const m of tour.matches)
            for (const v of m.videos) if (v.kind === "reel" && v.player_number === n) reels.push({ m, v });
        reels.reverse();
        return (
          <>
            <TeamTheme color={t.color} logo={t.watermark ?? t.logo} icon={t.logo} />
            <header className="team-head">
              <Link to={teamPath(t.slug)} className="muted back">← {t.name}</Link>
              <div className="team-title">
                {p?.photo && <img className="player-photo" src={imageUrl(p.photo)} alt="" />}
                <h1>
                  #{n} {p?.name ?? ""}
                </h1>
              </div>
            </header>
            {canEdit && <PlayerEditor team={t.slug} number={n} />}
            {line && (
              <section>
                <h2>Season</h2>
                <SeasonSpoiler t={t}>
                  <StatsTable stats={{ ...t.stats, players: [line], total: line }} total={false} />
                </SeasonSpoiler>
              </section>
            )}
            {reels.length > 0 && (
              <section>
                <h2>Highlights</h2>
                <div className="videos">
                  {reels.map(({ m, v }) => (
                    <VideoTile
                      key={videoKey(v)}
                      video={v}
                      title={`${formatDate(m.date)} vs ${m.opponent}`}
                      poster={m.opponent_logo}
                    />
                  ))}
                </div>
              </section>
            )}
          </>
        );
      }}
    </Gate>
  );
}

const VIDEO_TITLES: Record<Video["kind"], string> = {
  full: "Full match",
  condensed: "Condensed",
  reel: "Reel",
  season: "Season",
};

function MatchPage() {
  const { team = "", tournament = "", date = "", match = "" } = useParams();
  const res = useDoc<MatchDoc>(`match/${team}/${tournament}/${date}/${match}`);
  // Same id the team page checks: the match key is the URL tail.
  const key = `${tournament}/${date}/${match}`;
  const [revealed, reveal] = useRevealed(matchSpoiler(team, key));
  const spoilers = useReveals();
  // Opening the page is what counts: the match is seen and its videos
  // known from now on. What was new when it opened stays flagged on the
  // tiles while the page is open.
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const videoList = res.state === "ok" ? res.doc.videos.map(videoKey).join(" ") : "";
  useEffect(() => {
    if (res.state !== "ok") return;
    const m = res.doc;
    const added = newVideos(team, { ...m, key }, spoilers.revealed).map(videoKey);
    if (added.length) setFresh((prev) => new Set([...prev, ...added]));
    spoilers.reveal(seenId(team, key), ...m.videos.map((v) => knownId(team, key, videoKey(v))));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoList]);
  return (
    <Gate res={res}>
      {(m) => {
        const main = m.videos.filter((v) => v.kind !== "reel");
        const reels = m.videos.filter((v) => v.kind === "reel");
        return (
          <>
            <TeamTheme color={m.team_color} logo={m.team_watermark ?? m.team_logo} icon={m.team_logo} />
            <header className="team-head">
              <Link to={teamPath(m.team)} className="muted back">← {m.team_name}</Link>
              <h1>
                {m.team_name} vs {m.opponent}
              </h1>
              <p className="muted">
                {formatDate(m.date)} · {m.tournament_name}
                {m.index ? ` · Match ${m.index}` : ""}
              </p>
              <Spoiler revealed={revealed} onReveal={reveal} label="Reveal score">
                <Score m={m} />
              </Spoiler>
            </header>
            <LiveBanner streams={m.live ? [m.live] : []} teamName={m.team_name} />
            {main.length > 0 && (
              <section>
                <div className="videos videos-wide">
                  {main.map((v) => (
                    <VideoTile
                      key={videoKey(v)}
                      video={v}
                      title={v.stream ? "Live stream" : VIDEO_TITLES[v.kind]}
                      poster={m.opponent_logo}
                      isNew={fresh.has(videoKey(v))}
                    />
                  ))}
                </div>
              </section>
            )}
            {reels.length > 0 && (
              <section>
                <h2>Player reels</h2>
                <div className="videos">
                  {reels.map((v) => (
                    <VideoTile
                      key={videoKey(v)}
                      video={v}
                      title={v.player_label || "Reel"}
                      poster={m.team_logo}
                      isNew={fresh.has(videoKey(v))}
                    />
                  ))}
                </div>
              </section>
            )}
            {m.played !== false && m.stats.players.length > 0 && (
              <section>
                <h2>Stats</h2>
                <Spoiler revealed={revealed} onReveal={reveal} label="Reveal stats" note="Hidden so they don't give the result away.">
                  <StatsTable stats={m.stats} />
                </Spoiler>
              </section>
            )}
          </>
        );
      }}
    </Gate>
  );
}
