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
