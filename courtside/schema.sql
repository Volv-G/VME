-- Published documents: what VME pushes, read back by key.
--   teams                                   the team list
--   team/<team>                             a team's season: matches, stats, players
--   match/<team>/<tournament>/<date>/<match> one match in full
--   private/<team>                          full names, for the edit form only
CREATE TABLE IF NOT EXISTS docs (
  key TEXT PRIMARY KEY,
  json TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

-- Small images: player photos and team logos, kept here rather than in
-- object storage so the site needs nothing beyond the free D1 database.
CREATE TABLE IF NOT EXISTS images (
  key TEXT PRIMARY KEY,
  mime TEXT NOT NULL,
  data BLOB NOT NULL,
  updated_at INTEGER NOT NULL
);

-- Who may see what. A person signs in (Cloudflare Access proves their
-- email), opens the invite link, and asks to be a team's coach or one of
-- its players (a parent claiming their child). The admin approves or
-- declines; only approved rows grant anything. One person can hold
-- several rows: two children, or a coach who is also a parent.
CREATE TABLE IF NOT EXISTS members (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL,              -- lowercase
  team TEXT NOT NULL,               -- team slug, as in team/<team>
  role TEXT NOT NULL CHECK (role IN ('player', 'coach')),
  number INTEGER,                   -- jersey, for player claims only
  note TEXT NOT NULL DEFAULT '',    -- "Kate's mom", as typed when asking
  status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'declined')),
  requested_at INTEGER NOT NULL,
  decided_at INTEGER,
  decided_by TEXT
);
CREATE INDEX IF NOT EXISTS members_email ON members (email);
CREATE INDEX IF NOT EXISTS members_status ON members (status);

-- Site settings: `invite_code`, the secret part of the invite link.
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Changes to a player made on the site - a new photo, a corrected name -
-- waiting for VME to fetch them into its roster. VME deletes each row
-- once applied, then republishes, which is when the site shows it.
CREATE TABLE IF NOT EXISTS player_edits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  team TEXT NOT NULL,
  number INTEGER NOT NULL,
  name TEXT,                        -- full name; null = unchanged
  short_name TEXT,                  -- shown on the site; null = unchanged
  photo BLOB,                       -- JPEG; null = unchanged
  submitted_by TEXT NOT NULL,
  submitted_at INTEGER NOT NULL
);

-- What someone chose to see (src/Spoilers.tsx ids such as
-- "match:<team>/<key>") and the reels they have watched ("seen:..."),
-- kept per person so every device agrees.
CREATE TABLE IF NOT EXISTS reveals (
  email TEXT NOT NULL,
  id TEXT NOT NULL,
  revealed_at INTEGER NOT NULL,
  PRIMARY KEY (email, id)
);

-- Matches added to, moved on or taken off the schedule here - by a coach
-- or the admin on the site, or from the streaming phone - waiting for VME
-- to apply them (library/schedule.py). VME deletes each row once applied
-- and republishes, which is when the match shows as upcoming.
CREATE TABLE IF NOT EXISTS schedule_edits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  team TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('add', 'update', 'delete')),
  key TEXT,                         -- <tournament>/<date>/<match>, for update/delete
  tournament TEXT,                  -- folder or name as typed, for add
  date TEXT,                        -- YYYY-MM-DD
  time TEXT,                        -- HH:MM local, or null
  opponent TEXT,
  submitted_by TEXT NOT NULL,       -- an email, or "phone"
  submitted_at INTEGER NOT NULL
);

-- Streams going on now, as the phone reports them (server/phone.ts): one
-- row per phone match, refreshed every few minutes while it streams.
CREATE TABLE IF NOT EXISTS live (
  team TEXT NOT NULL,
  id TEXT NOT NULL,                 -- the phone's match slug
  match_key TEXT,                   -- <tournament>/<date>/<match> when the phone knows it
  opponent TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  ended_at INTEGER,
  PRIMARY KEY (team, id)
);

-- The score as the phone keeps it (server/schedule.ts), sent on every
-- point. Shown for a match until VME has tagged it itself - VME's score,
-- from the full video, then takes over. Scores only: the phone's taps are
-- not stats anyone should read.
CREATE TABLE IF NOT EXISTS phone_scores (
  team TEXT NOT NULL,
  id TEXT NOT NULL,                 -- the phone's match slug
  match_key TEXT,                   -- <tournament>/<date>/<match> when the phone knows it
  opponent TEXT NOT NULL DEFAULT '',
  date TEXT,
  score TEXT NOT NULL,              -- {home_sets, away_sets, sets, home, away, ended}
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (team, id)
);
