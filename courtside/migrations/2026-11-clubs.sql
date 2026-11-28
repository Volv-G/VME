-- Once, on a database made before clubs (schema.sql has these columns
-- for a new one). Not idempotent: a second run fails with "duplicate
-- column", which is harmless.
--   npx wrangler d1 execute vme-site --remote --file=migrations/2026-11-clubs.sql
ALTER TABLE schedule_edits ADD COLUMN club TEXT;
ALTER TABLE schedule_edits ADD COLUMN color TEXT;
