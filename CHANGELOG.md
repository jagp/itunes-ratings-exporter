# Changelog

## 1.1.0 — 2026-08-03

Adds Spotify import. The exporter's CSV schema was always intended to be
matching-friendly; this is the companion tool that consumes it.

**[untested]** marks behaviour covered by unit tests but never exercised
against the live API. The largest verified real run is 20 tracks; no
full-library run has completed, and nothing built to survive an interruption
has yet survived a real one.

### Added

- `spotify-import` subcommand — turns an exported ratings CSV into a Spotify
  playlist. Authorization uses OAuth Authorization Code with PKCE, so no client
  secret is needed; tokens are cached in
  `~/.itunes-ratings-exporter/spotify-token.json`.
- Conservative fuzzy matching on title, artist, and runtime. A wrong song in a
  playlist is silent and may go unnoticed for months, while a dropped song lands
  in the report where it can be seen and fixed — so matching requires title and
  artist to be independently plausible, and treats a 15s-plus runtime gap as
  proof of a different recording regardless of how well the metadata agrees.
  Tunable with `--min-score`.
- `spotify_import_report.csv` — one row per track, status `matched`, `rejected`
  (with the near miss shown), or `not_found`. Written even when a run fails
  partway, so matching work is never lost.
- `--resume` **[untested]** — reuses `matched` and `rejected` results from a previous report
  and searches only what is left. A reused match still goes into the playlist,
  it just costs no quota. `not_found` tracks *are* retried, since a miss can be
  a bad search rather than a real absence, but they run last so a second
  interruption falls on tracks nobody has tried yet. Rows are keyed on
  `persistent_id`, falling back to title and artist.
- Per-track progress logging **[untested]**, so a run cut short by a crash or a
  spent quota still leaves a scrollback record of exactly what was resolved.
  `--quiet` restores totals-only output.
- `--dry-run`, `--limit`, `--public`, `--name`, `--report`, `--client-id`.
- Exit codes `4` (input CSV missing or malformed), `5` (authorization failed),
  `6` (Spotify API failure), `7` **[untested]** (request quota spent — wait the
  reported time and re-run with `--resume`).

### Fixed

- Migrated to the February 2026 Spotify Web API. The retired endpoints answer
  **403 Forbidden** rather than 404, which makes a removed endpoint look
  indistinguishable from a permissions problem:
  `POST /users/{id}/playlists` → `POST /me/playlists`,
  `/playlists/{id}/tracks` → `/playlists/{id}/items`, and search `limit` now
  caps at 10 instead of 50.
- A spent rolling quota is reported instead of slept through **[untested]**.
  The quota response itself was observed live; the new fail-fast path was not.
  Spotify answers
  one with a `Retry-After` of several hours — longer than the access token
  lives — so waiting it out only traded a visible failure for a later 401 while
  appearing to hang. Waits past two minutes now stop and say when to come back.
- Cached tokens record their scopes and force a fresh consent when those scopes
  change. A refreshed token carrying stale scopes fails as a bare 403 with no
  indication that re-consenting is the fix.

## 1.0.0

Initial release. Reads `iTunes Music Library.xml` and exports `tracks.csv`,
`rated.csv`, `playlists.csv`, and `library.json`, including libraries hosted on
a NAS via a mapped drive or UNC path.
