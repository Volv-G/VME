-- Published documents: what VME pushes, read back by key.
--   teams                                   the team list
--   team/<team>                             a team's season: matches, stats, players
--   match/<team>/<tournament>/<date>/<match> one match in full
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
