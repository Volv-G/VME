import { Link, Route, Routes, useParams } from "react-router-dom";
import type { MatchDoc, MatchSummary, TeamDoc, TeamsDoc, Video } from "../shared/docs";
import type { Me } from "../shared/api";
import { formatDate, imageUrl, MeContext, savedInvite, useApi, useDoc, useMe } from "./api";
import { Crest, Gate, matchPath, playerPath, teamPath } from "./common";
import { AdminPage } from "./Admin";
import { JoinPage } from "./Join";
import { PlayerEditor } from "./PlayerEditor";
import { StatsTable } from "./StatsTable";
import { YouTube } from "./YouTube";

export function App() {
  const me = useApi<Me>("/api/me");
  const value = { me: me.state === "ok" ? me.doc : null, reload: me.reload };
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
      <Link to="/" className="brand">Courtside</Link>
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
  const invite = savedInvite.get();
  const waiting = (me?.memberships ?? []).filter((m) => m.status !== "approved");
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
              This site is for team families. To get in, open the invite link your team shared
              {invite ? (
                <>
                  {" "}- or <Link to={`/join/${invite}`}>use the one you opened before</Link>
                </>
              ) : null}
              .
            </p>
          )
        }
      </Gate>
      {invite && (me?.memberships.length ?? 0) > 0 && (
        <p className="small">
          <Link to={`/join/${invite}`}>Ask for access to another player or team</Link>
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
    </span>
  );
}

function TeamPage() {
  const { team = "" } = useParams();
  const res = useDoc<TeamDoc>(`team/${team}`);
  return (
    <Gate res={res}>
      {(t) => (
        <>
          <header className="team-head" style={{ borderBottomColor: t.color }}>
            <Link to="/" className="muted back">← Teams</Link>
            <div className="team-title">
              <Crest src={t.logo} color={t.color} label={t.name} size={64} />
              <h1>{t.name}</h1>
            </div>
          </header>

          {t.season_reels.length > 0 && (
            <section>
              <h2>Season highlights</h2>
              <div className="videos">
                {t.season_reels.map((v) => (
                  <YouTube key={v.youtube_id} id={v.youtube_id} title={`${v.player_label}: season`} />
                ))}
              </div>
            </section>
          )}

          <section>
            <h2>Matches</h2>
            {t.tournaments.map((tour) => (
              <div key={tour.slug} className="tournament">
                <h3>{tour.name}</h3>
                <div className="list">
                  {[...tour.matches].reverse().map((m) => (
                    <Link key={m.key} to={matchPath(t.slug, m.key)} className="row">
                      <Crest src={m.opponent_logo} label={m.opponent} size={32} />
                      <span className="row-main">
                        <span>vs {m.opponent}</span>
                        <span className="muted small">
                          {formatDate(m.date)}
                          {m.index ? ` · Match ${m.index}` : ""}
                          {m.videos.length ? ` · ${m.videos.length} video${m.videos.length === 1 ? "" : "s"}` : ""}
                        </span>
                      </span>
                      <Score m={m} />
                    </Link>
                  ))}
                </div>
              </div>
            ))}
          </section>

          <section>
            <h2>Season stats</h2>
            <p className="muted small">{t.stats.matches} matches. Tap a column to sort.</p>
            <StatsTable stats={t.stats} />
          </section>

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
      )}
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
        const season = t.season_reels.filter((v) => v.player_number === n);
        const reels: { m: MatchSummary; v: Video }[] = [];
        for (const tour of t.tournaments)
          for (const m of tour.matches)
            for (const v of m.videos) if (v.kind === "reel" && v.player_number === n) reels.push({ m, v });
        reels.reverse();
        return (
          <>
            <header className="team-head" style={{ borderBottomColor: t.color }}>
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
                <StatsTable stats={{ ...t.stats, players: [line], total: line }} total={false} />
              </section>
            )}
            {(season.length > 0 || reels.length > 0) && (
              <section>
                <h2>Highlights</h2>
                <div className="videos">
                  {season.map((v) => (
                    <YouTube key={v.youtube_id} id={v.youtube_id} title="Season highlights" />
                  ))}
                  {reels.map(({ m, v }) => (
                    <YouTube key={v.youtube_id} id={v.youtube_id} title={`${formatDate(m.date)} vs ${m.opponent}`} />
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
  return (
    <Gate res={res}>
      {(m) => {
        const main = m.videos.filter((v) => v.kind !== "reel");
        const reels = m.videos.filter((v) => v.kind === "reel");
        return (
          <>
            <header className="team-head">
              <Link to={teamPath(m.team)} className="muted back">← {m.team_name}</Link>
              <h1>
                {m.team_name} vs {m.opponent}
              </h1>
              <p className="muted">
                {formatDate(m.date)} · {m.tournament_name}
                {m.index ? ` · Match ${m.index}` : ""}
              </p>
              <Score m={m} />
            </header>
            {main.length > 0 && (
              <section>
                <div className="videos videos-wide">
                  {main.map((v) => (
                    <YouTube key={v.youtube_id} id={v.youtube_id} title={VIDEO_TITLES[v.kind]} />
                  ))}
                </div>
              </section>
            )}
            {reels.length > 0 && (
              <section>
                <h2>Player reels</h2>
                <div className="videos">
                  {reels.map((v) => (
                    <YouTube key={v.youtube_id} id={v.youtube_id} title={v.player_label || "Reel"} />
                  ))}
                </div>
              </section>
            )}
            <section>
              <h2>Stats</h2>
              <StatsTable stats={m.stats} />
            </section>
          </>
        );
      }}
    </Gate>
  );
}
