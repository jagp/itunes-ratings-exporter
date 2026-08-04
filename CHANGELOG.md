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
  in the queue where it can be seen and retried — so matching requires title and
  artist to be independently plausible, and treats a 15s-plus runtime gap as
  proof of a different recording regardless of how well the metadata agrees.
  Tunable with `--min-score`.
- A queue and a log rather than a report **[untested]**.
  `spotify_import_queue.csv` holds the tracks not yet in Spotify;
  `spotify_import_log.csv` holds the ones confirmed in the playlist. A track is
  in exactly one, so `queue + log` is always the whole library and the queue's
  length is the work remaining. The export itself is only read — the queue is a
  copy of it.
- Every run is therefore a resume, with no flag to remember: re-running the
  same command drains whatever is left. Tracks leave the queue only once
  Spotify has accepted them, and matches are delivered in batches of 100 (the
  API maximum) as they accumulate, so an interruption leaves a real partial
  playlist rather than nothing. Later runs top up the same playlist instead of
  starting another.
- Tracks searched but unresolved stay queued with their verdict — `rejected`
  (near miss shown) or `not_found` — so a re-run retries them after a lowered
  `--min-score`, while already-matched tracks cost no further search quota.
- `--restart` **[untested]** rebuilds the queue from the export, keeping the
  log so nothing is imported twice.
- Per-track progress logging **[untested]**, so a run cut short by a crash or a
  spent quota still leaves a scrollback record of exactly what was resolved.
  `--quiet` restores totals-only output.
- `--rate` paces requests to Spotify (default 2/second) rather than only
  backing off once a 429 arrives. Precautionary: no run has yet produced
  evidence that it helps — see "Known limits" below.
- Every run reports how many Spotify requests it spent, and at what pace, so
  that the nature of the quota ceiling can be measured rather than guessed.
- A resume searches never-seen tracks before retrying recorded near misses.
  Requests, not tracks, are the scarce resource, and re-examining a known miss
  costs the same three searches as an unseen track while being unable to add
  anything to the playlist. The queue file itself stays in library order.
- `--dry-run`, `--limit`, `--public`, `--name`, `--quiet`, `--client-id`.
- Exit codes `4` (input CSV missing or malformed), `5` (authorization failed),
  `6` (Spotify API failure), `7` **[untested]** (request quota spent — wait the
  reported time and re-run the same command; the queue holds what is left).

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
- Cached tokens record their scopes and force a fresh consent when the cache
  cannot cover what the run needs. A refreshed token missing a scope fails as a
  bare 403 with no indication that re-consenting is the fix. Coverage is tested
  by containment rather than string equality: a cache holding *more* scopes
  than the run requires is perfectly usable, and comparing for equality dragged
  the user through a browser consent to end up with strictly fewer permissions
  than they already had. Scope strings are unordered, so equality was fragile
  on ordering alone.

### Known limits

- A full 826-track run reached track 593 and put **500 tracks** in the playlist
  before Spotify answered `429` with a `Retry-After` of 23.9 hours. The queue
  and log survived it intact (326 + 500 = 826, no overlap), and re-running
  continues where it stopped.
- **What that ceiling actually is remains unknown.** Two hypotheses fit the
  evidence equally well: a fixed request budget per rolling day, or a rate
  limit with escalating penalties where 2/second is still too fast. Nothing
  observed so far separates them, and no run has recorded how many requests it
  made, so even the size of the ceiling is only bounded (~770–1,780) rather
  than measured.
- Pacing has **no evidence supporting it** in this workload. Across the entire
  826-track run the short-burst retry path fired zero times and the pace never
  widened, so the mechanism `--rate` guards against was never observed. It is
  retained as a cheap precaution, not as a demonstrated fix; the earlier
  improvement from 0 to 500 tracks imported was due to incremental flushing
  alone.
- Runs now report requests spent alongside the pace they were spent at. The
  two hypotheses predict different things about that pair — a budget caps the
  count whatever the pace, a rate limit lets a slower run reach a higher count
  — so consecutive runs at different `--rate` values will settle it.
- Matching spends up to three searches per track, and the ~87 tracks that
  resolved to `rejected`/`not_found` consumed roughly 28% of the run's
  requests while adding nothing. Regardless of which hypothesis holds, fewer
  searches per track is the lever that costs least; Spotify's extended quota
  is the one that raises the ceiling.

## 1.0.0

Initial release. Reads `iTunes Music Library.xml` and exports `tracks.csv`,
`rated.csv`, `playlists.csv`, and `library.json`, including libraries hosted on
a NAS via a mapped drive or UNC path.
