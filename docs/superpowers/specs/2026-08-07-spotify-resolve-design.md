# spotify-resolve: a UI for the tracks the import could not place

**Date:** 2026-08-07
**Status:** Approved direction (modality and Tier-1 semantics confirmed by user;
remaining details proceed under stated assumptions — background session).

## Problem

After `spotify-import` has drained every safe match, the queue still holds a
remainder: `rejected` near misses and `not_found` tracks. Today the only levers
are re-running with a lower `--min-score` (global, risky) or editing the CSV by
hand. The user observed that many rejections trace back to burned CD mixes,
where the library-side album is really a playlist name.

**Key finding that shapes the design:** the matcher never reads the album
field. Scoring is title (0.45) + artist (0.35) + duration (0.20) and the
search queries use only title/artist. So mix-CD albums cannot directly cause a
rejection; those tracks fail on the **duration hard-veto** (a burned rip's
runtime drifts ≥15 s from the canonical recording) or land a hair under
`--min-score`. Consequence: for most of these, the *right candidate is already
recorded in the queue row* — resolving them needs no new searches at all.

## Tiers, reinterpreted as resolution effort

| Tier | Meaning | Resolution |
| --- | --- | --- |
| 1 | Stored near-miss candidate is almost certainly right (title and artist both re-score ≥ 0.85) | Bulk review: one list, one confirmation, zero quota |
| 2 | Needs human judgement, but a 1:1 Spotify track exists | Side-by-side card, one track at a time; optional fresh candidate search |
| 3 | Genuinely unavailable on Spotify | New durable status `unavailable`: stops consuming search quota on every resume — this *is* the way the app handles them |

## UI modality (user-confirmed)

A terminal prompt loop: new subcommand

```
python -m itunes_ratings_exporter spotify-resolve [--csv PATH]
    [--client-id ID] [--rate F] [--include-unavailable]
```

stdlib-only, `input()`-driven, testable exactly like the existing suite.

## Data model changes

`QUEUE_FIELDS` gains two columns, and one behavior changes:

- `spotify_duration_ms`, `spotify_album` — recorded for every candidate so the
  resolver can show a true side-by-side and re-score without the network.
- `_record_match` keeps the candidate `spotify_uri` on **rejected** rows
  (today it blanks it). Safe: delivery is gated on
  `status == "matched" and spotify_uri`, both conditions.
- New status value `unavailable`.

Backward compatibility: old queue files simply lack the new columns; readers
treat missing as empty (T1 then needs `[c]` or falls to T2 when duration is
unknown — acceptable degradation).

### Invariant amendment (documented trade-off)

README today: "the queue's line count is literally what is left to do."
`unavailable` rows stay in the queue file — preserving the stronger invariant
"a track is in exactly one file, queue + log = whole library" — but are
excluded from the work loop. The README sentence and the run summary change to
report "N still queued (of which M unavailable)".

## Resolve flow (`spotify/resolver.py`)

**Phase 1 — Tier 1 bulk review.** Partition `rejected` rows: those whose
stored candidate re-scores ≥ 0.85 on both title and artist similarity are
listed with scores. Prompt: apply all / `skip 3,7` to un-tick / skip phase.
Accepting sets `status=matched` (URI already present).

**Phase 2 — Tier 2 walkthrough.** Remaining `rejected` and all `not_found`
rows, one card each:

```
[ 12/143] REJECT 0.68  (title 0.97  artist 1.00  duration 0.00)
  iTunes : Thunder Road — Bruce Springsteen
           album: Summer Mix 2003   4:49
  Spotify: Thunder Road — Bruce Springsteen
           album: Born to Run       4:48
  [a]ccept  [c]andidates  [u]navailable  [k]eep  [q]uit >
```

- `[a]` accept the stored candidate (absent for `not_found` — hidden then).
- `[c]` run fresh searches, show the top ~5 scored candidates, pick by number
  or return. The **only** action that costs quota; Spotify auth is lazy —
  first `[c]` triggers it. A session that never uses `[c]` is fully offline.
- `[u]` mark `unavailable` (Tier 3). `[k]` keep as is. `[q]` quit; everything
  decided so far is saved.

The queue file is rewritten after every decision — quitting or crashing
mid-session loses nothing.

## Delivery: no second write path

Resolve edits verdicts only. `run_import` already delivers carried-over
`matched` rows before spending any search quota, so the resolver's closing
message is: "N accepted — re-run spotify-import to add them to the playlist."
Batch-of-100, deliver-then-log, and the crash-duplication guarantees stay in
exactly one place.

## Component changes

| File | Change |
| --- | --- |
| `spotify/matcher.py` | `top_candidates(row, client, n)` — like `find_match` but returns all scored candidates, best first, deduplicated by URI |
| `spotify/importer.py` | new queue columns; keep URI on rejects; exclude `unavailable` from `todo`; summary gains `unavailable` count |
| `spotify/resolver.py` | new: partition, card rendering, prompt loop, phase logic; I/O injected (`input_fn`, `print_fn`) for tests |
| `cli.py` | `spotify-resolve` subcommand, lazy auth, exit codes reused (4 input, 5 auth, 6 API, 7 quota) |
| `README.md` | new section; amend the queue-invariant sentence |

## Error handling

- Quota/API errors during `[c]`: the card is kept (`keep`), session continues
  offline; message explains searches are unavailable.
- Invalid key at a prompt: re-prompt with the legend.
- Missing/empty queue: friendly "nothing to resolve" and exit 0.

## Testing

`tests/test_spotify_resolver.py` in the existing style: fake client, scripted
`input_fn`, temp queue files. Covers T1 partition boundaries (0.85 both-axes),
bulk skip syntax, each T2 key, lazy-auth-only-on-`[c]`, unavailable excluded
from a subsequent `run_import` todo, old-schema queue files, and quit-saves.

## Out of scope

Web UI, editing the iTunes library itself, automatic un-marking of
`unavailable` rows (revisit via `--include-unavailable`).
