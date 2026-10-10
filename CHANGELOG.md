# Changelog

## 1.1.0 — 2026-08-03

- added self-explanatory arguments - `--dry-run`, `--limit`, `--public`, `--name`, `--quiet`, `--client-id`.

- Migrated to the February 2026 Spotify Web API. The retired endpoints answer
  **403 Forbidden** rather than 404, which makes a removed endpoint look
  indistinguishable from a permissions problem:
  `POST /users/{id}/playlists` → `POST /me/playlists`,
  `/playlists/{id}/tracks` → `/playlists/{id}/items`, and search `limit` now
  caps at 10 instead of 50.

- Cached tokens record their scopes and force a fresh consent when the cache
  cannot cover what the run needs. A refreshed token missing a scope fails as a
  bare 403 with no indication that re-consenting is the fix.

### Known limits

- Spotify maintains a request-limiting mechanism, returning a error 429 following a successful series of API calls (a string of 200s followed by 429s and directive to retry the request after a varying amount of time)
- Several hypothetical methods were tested to discover the request-limiting behavior withuot a conclusion that could be empirically supported. The limit from a cold start is recorded at below, but near, 500 requests when no prior API calls have been attempted
- Matching spends up to three searches per track

## 1.0.0

Initial release. Reads `iTunes Music Library.xml` and exports `tracks.csv`,
`rated.csv`, `playlists.csv`, and `library.json`, including libraries hosted on
a NAS via a mapped drive or UNC path.
