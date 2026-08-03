# Spotify Playlist Import — Design

**Date:** 2026-08-02
**Status:** Approved design

## Purpose

Take the CSV this tool already exports (`rated.csv` by default) and recreate it
as a playlist in the user's Spotify account. This is the "companion tool" the
original design anticipated: the export schema was deliberately made
matching-friendly, and this feature consumes it.

The user's rated iTunes library is the thing they care about preserving. Getting
those tracks into Spotify as a playlist makes a decade of ratings usable in the
service they actually listen to now.

## Non-goals

- No writing ratings back to Spotify. Spotify has no per-track star rating; the
  only rating-like primitive is Liked Songs, which is boolean. Out of scope.
- No syncing. Each run is a one-way, one-shot import.
- No audio fingerprinting or third-party match services (MusicBrainz, AcoustID).
  Matching uses Spotify's own search endpoint and the metadata already in the
  CSV.
- No third-party Python packages. Standard library only, matching the rest of
  the project.
- No importing the *other* CSVs (`playlists.csv`) as multiple Spotify playlists.
  One CSV in, one playlist out.

## User flow

1. User creates a Spotify app at <https://developer.spotify.com/dashboard> and
   adds `http://127.0.0.1:8888/callback` as a redirect URI. They copy the
   Client ID.
2. User sets `SPOTIFY_CLIENT_ID` (or passes `--client-id`).
3. User runs:
   ```
   python -m itunes_ratings_exporter spotify-import --min-stars 4
   ```
4. A browser opens for Spotify consent. After approval the CLI receives the
   code on the loopback listener and exchanges it for tokens.
5. The CLI searches Spotify for each track, creates the playlist, adds the
   matches, and writes a report CSV.

Tokens are cached so steps 4 is skipped on subsequent runs until the refresh
token is revoked.

## Authentication

**Authorization Code with PKCE.** A desktop CLI is a public client and cannot
keep a client secret, so PKCE is the correct flow — the user only needs a
Client ID, never a secret.

- Redirect URI: `http://127.0.0.1:8888/callback` (loopback, per Spotify's
  current rules — `localhost` is no longer accepted, the literal IP is).
- Scopes: `playlist-modify-private playlist-modify-public`.
- The CLI starts a single-request `http.server` on 127.0.0.1:8888, opens the
  authorize URL with `webbrowser`, waits for the redirect, then shuts down.
- A random `state` value is generated and verified on the callback to reject
  cross-site request forgery against the loopback listener.
- Token cache: `~/.itunes-ratings-exporter/spotify-token.json`, written with
  mode `0600` where the platform supports it. Holds the refresh token, access
  token, and absolute expiry.
- On each run: if a cached access token is unexpired, use it; else refresh; else
  run the full browser flow.

## Architecture

New subpackage `itunes_ratings_exporter/spotify/`, four modules, each
independently testable without network access:

| Module | Responsibility |
|---|---|
| `auth.py` | PKCE flow, loopback callback server, token cache, refresh |
| `client.py` | Thin Spotify Web API wrapper over `urllib`: `search`, `current_user`, `create_playlist`, `add_tracks`. Retries on 429/5xx |
| `matcher.py` | Turn one CSV row into a Spotify track URI (or a miss), with a confidence score |
| `importer.py` | Orchestration: read CSV, filter, match, create playlist, write report |

`cli.py` gains a subcommand layer. The existing flat invocation stays working
(see CLI compatibility below).

### Data flow

```
rated.csv ──> csv.DictReader ──> filter(min_stars) ──> matcher.find_match ──┐
                                                                            │
                          ┌─────────────────────────────────────────────────┘
                          v
              matched: [track_uri]        unmatched: [row + reason]
                          │                          │
                          v                          v
          client.create_playlist +        spotify_import_report.csv
          client.add_tracks (100/batch)
```

## Matching

The hard part. iTunes metadata and Spotify metadata disagree constantly:
remasters, feature-artist punctuation, "Pt. 2" vs "Part 2", explicit/clean
variants, live versions, regional catalogue differences.

**Query strategy**, in order, stopping at the first that yields candidates:

1. Field-qualified: `track:"<title>" artist:"<artist>"`
2. Field-qualified with the title stripped of parenthetical suffixes
   (`(Remastered 2011)`, `(feat. X)`, `- Live`)
3. Freeform: `<title> <artist>`

**Scoring.** Each candidate gets a score in `0.0..1.0` from three signals:

- **Title similarity** — normalized (casefold, strip punctuation/diacritics,
  drop parenthetical suffixes) then compared with `difflib.SequenceMatcher`.
- **Artist similarity** — same normalization; matches against any artist on the
  Spotify track, so "A feat. B" in iTunes still matches when Spotify lists B
  separately.
- **Duration proximity** — within 3s scores 1.0, falling linearly to 0.0 at 15s
  apart. Unknown on either side is neutral (0.5). This is the signal that
  catches wrong-version matches (radio edit vs album, live vs studio) that
  title and artist alone cannot.

Two of these act as **vetoes** rather than weights, because weighting cannot
express them. A perfect title and artist alone total 0.80, which clears any
sensible threshold — so "Creep" would match "Creep - Live" despite a 72-second
runtime gap. Therefore:

- Title or artist below 0.6 rejects outright: a very strong signal on one must
  not carry a weak signal on the other.
- A duration score of exactly 0 rejects outright: both runtimes are known and
  irreconcilably far apart, which is proof of a different recording.

The acceptance rule combining these lives in one small, clearly marked function,
`accept_match()`, in `matcher.py`. It is deliberately isolated because it is the
feature's central value judgement:

> **False positive** — a wrong song silently lands in your playlist. You may not
> notice for months.
> **False negative** — a right song is dropped, but it is listed in the report
> CSV with the near-miss candidate, so you can see and fix it.

The default therefore leans conservative: reject when uncertain, since a miss is
visible and recoverable while a bad match is invisible. `--min-score` lets the
user override the threshold per run without editing code.

## CLI

```
itunes-ratings-exporter [--library PATH] [--out DIR]          # unchanged
itunes-ratings-exporter spotify-import [options]              # new
```

**CLI compatibility.** The existing invocation has no subcommand, and breaking
it would break the documented interface. `cli.main()` therefore dispatches on
`argv[0]`: if it equals a known subcommand name, route there; otherwise parse
with the original export parser. This keeps `--library`/`--out` working exactly
as before and needs no `export` subcommand to be typed.

`spotify-import` options:

| Option | Default | Meaning |
|---|---|---|
| `--csv PATH` | `export/rated.csv` | Input CSV (any file with the exporter's header) |
| `--name NAME` | `iTunes Ratings <YYYY-MM-DD>` | Playlist name |
| `--min-stars N` | `4` | Only import tracks rated at least N stars |
| `--limit N` | none | Import at most N tracks (useful for a trial run) |
| `--public` | off | Create a public playlist; private otherwise |
| `--dry-run` | off | Match and report, but create nothing on Spotify |
| `--min-score F` | `0.72` | Match acceptance threshold, `0.0`–`1.0` |
| `--client-id ID` | `$SPOTIFY_CLIENT_ID` | Spotify app client ID |
| `--report PATH` | `<csv dir>/spotify_import_report.csv` | Where the report goes |

Each run creates a new playlist. There is no update-in-place mode: reruns are
expected to be rare, and silently mutating an existing playlist the user may
have since curated by hand is worse than leaving a duplicate they can delete.

## Report output

`spotify_import_report.csv` — one row per input track, always written (including
under `--dry-run`), so the run is auditable:

| Column | Meaning |
|---|---|
| `title`, `artist`, `album`, `rating_stars` | Echoed from the input row |
| `status` | `matched`, `rejected`, or `not_found` |
| `score` | Best candidate's score, blank if nothing was found |
| `spotify_uri` | URI of the accepted match, blank otherwise |
| `spotify_title`, `spotify_artist` | Best candidate's metadata — for `rejected` rows this is what the user needs to judge the near miss |

## Error handling

| Condition | Behaviour |
|---|---|
| CSV missing or unreadable | Exit `4`, name the path and suggest running the export first |
| CSV lacks required columns | Exit `4`, name the missing columns |
| No client ID | Exit `5` with setup instructions (dashboard URL, redirect URI to register) |
| Consent denied / callback timeout (5 min) | Exit `5` |
| Refresh token rejected | Delete the cache and fall back to the full browser flow once; exit `5` if that also fails |
| HTTP 429 | Sleep for `Retry-After` and retry, up to 5 times per request |
| HTTP 5xx | Exponential backoff, up to 3 times |
| Persistent API failure mid-import | Exit `6`, but write the report first so matching work is not lost |
| Zero tracks matched | Exit `0`, create nothing, say so plainly |

Exit codes continue the existing scheme (`0` success, `1` bad XML, `2` library
missing, `3` output unwritable) with `4` input CSV, `5` auth, `6` API.

## Testing

No network in tests. `client.py` takes an injectable transport (a callable
taking a `urllib.request.Request` and returning `(status, headers, body)`), so
tests pass a fake.

- `test_spotify_matcher.py` — normalization, the three query strategies,
  scoring, and the accept/reject boundary. Includes the real-world cases:
  remaster suffixes, `feat.` handling, live-version rejection by duration,
  unicode/diacritic titles.
- `test_spotify_client.py` — request shape, 429 `Retry-After` honoured, 5xx
  backoff, 100-URI batching for `add_tracks`, token passed as bearer.
- `test_spotify_auth.py` — PKCE verifier/challenge derivation (S256 against a
  known vector), state mismatch rejected, token cache round-trip, expiry logic.
- `test_spotify_importer.py` — end-to-end with a fake client: filtering by
  stars, `--limit`, report contents for each status, `--dry-run` creates
  nothing.
- `test_cli.py` — additions asserting the original no-subcommand invocation
  still works, plus the new subcommand's argument wiring and exit codes.

## Security notes

- No client secret is stored, by construction (PKCE).
- The token cache holds a refresh token; it is written `0600` and lives under
  the user's home directory, not in the repository or the export directory.
- The loopback listener binds `127.0.0.1` only, accepts exactly one request,
  and verifies `state` before accepting the code.
- Tokens are never printed, including in error messages.
