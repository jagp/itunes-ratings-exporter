"""Turn an exported iTunes CSV into a Spotify playlist.

State lives in two files rather than in a report the tool reads back and
reasons about:

``spotify_import_queue.csv``
    Tracks not yet in Spotify. Derived from the export on the first run and
    drained thereafter.
``spotify_import_log.csv``
    Tracks confirmed to have reached the playlist. Append-only.

A line is in exactly one of them, so ``queue + log`` is always the whole
library and the queue's length is literally the work remaining. That makes
every run a resume: there is no state to reconcile, only a list to drain.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional, Union

from .client import ADD_TRACKS_BATCH
from .matcher import DEFAULT_MIN_SCORE, candidate_artists, find_match

REQUIRED_COLUMNS = ("title", "artist")

PLAYLIST_URL = "https://open.spotify.com/playlist/{}"

# The queue carries everything matching needs (duration included -- a later
# run has to be able to score a track without re-reading the export) plus the
# verdict so far. persistent_id is kept rather than overwritten with the
# Spotify ID: it is the only stable key back to the iTunes library.
QUEUE_FIELDS = [
    "persistent_id",
    "title",
    "artist",
    "album",
    "rating_stars",
    "duration_ms",
    "status",
    "score",
    "spotify_uri",
    "spotify_title",
    "spotify_artist",
    "spotify_album",
    "spotify_duration_ms",
    "attempts",
]

LOG_FIELDS = QUEUE_FIELDS + ["added_to"]

PENDING = "pending"

# A human's verdict, not the matcher's: the track is not on Spotify, so no
# amount of re-searching will place it. Rows keep their place in the queue
# file -- queue + log stays the whole library -- but the import's work loop
# skips them, which is what stops every resume from spending quota proving
# the same absence again. spotify-resolve sets and unsets it.
UNAVAILABLE = "unavailable"


class ImportInputError(RuntimeError):
    """The input CSV is missing, unreadable, or the wrong shape."""


def default_playlist_name(today: "Optional[date]" = None) -> str:
    return "iTunes Ratings {}".format((today or date.today()).isoformat())


def default_queue_path(csv_path: "Union[str, Path]") -> Path:
    return Path(csv_path).parent / "spotify_import_queue.csv"


def default_log_path(csv_path: "Union[str, Path]") -> Path:
    return Path(csv_path).parent / "spotify_import_log.csv"


def read_rows(
    csv_path: "Union[str, Path]", min_stars: int = 0, limit: "Optional[int]" = None
) -> "list[dict[str, Any]]":
    """Load the export, keeping only tracks rated at least ``min_stars``."""
    path = Path(csv_path)
    if not path.is_file():
        raise ImportInputError(
            "No CSV at {}. Run the exporter first, or point --csv at one.".format(path)
        )
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
            if missing:
                raise ImportInputError(
                    "{} is missing required column(s): {}".format(path, ", ".join(missing))
                )
            if min_stars > 0 and "rating_stars" not in fieldnames:
                raise ImportInputError(
                    "{} has no rating_stars column, so --min-stars cannot be "
                    "applied. Use --min-stars 0 to import every row.".format(path)
                )
            rows = [r for r in reader if _stars(r) >= min_stars]
    except OSError as exc:
        raise ImportInputError("Could not read {}: {}".format(path, exc))
    return rows[:limit] if limit else rows


def _stars(row: "dict[str, Any]") -> int:
    try:
        return int(row.get("rating_stars") or 0)
    except (TypeError, ValueError):
        return 0


def _write(path: "Union[str, Path]", fields: "list[str]", rows: "list[dict[str, Any]]") -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def _append(path: "Union[str, Path]", fields: "list[str]", rows: "list[dict[str, Any]]") -> None:
    """Append rows, writing a header only when the file is new."""
    target = Path(path)
    new = not target.is_file() or target.stat().st_size == 0
    with open(target, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if new:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def _read(path: "Union[str, Path]", what: str) -> "list[dict[str, Any]]":
    target = Path(path)
    if not target.is_file():
        return []
    try:
        with open(target, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except OSError as exc:
        raise ImportInputError("Could not read the {} at {}: {}".format(what, target, exc))


def read_queue(path: "Union[str, Path]") -> "list[dict[str, Any]]":
    return _read(path, "queue")


def read_log(path: "Union[str, Path]") -> "list[dict[str, Any]]":
    return _read(path, "log")


def queue_row(row: "dict[str, Any]") -> "dict[str, Any]":
    """A work item: the export's own fields plus an unresolved verdict."""
    item = {k: row.get(k, "") for k in QUEUE_FIELDS}
    item["status"] = PENDING
    item["score"] = ""
    item["spotify_uri"] = ""
    item["spotify_title"] = ""
    item["spotify_artist"] = ""
    item["attempts"] = "0"
    return item


def attempts(row: "dict[str, Any]") -> int:
    """How many runs have already spent searches on this track.

    Rows written before the column existed are inferred from their status: a
    pending row has never been searched, and anything else has been searched
    at least once. That keeps an existing queue from being re-sorted into a
    lie the first time a newer build reads it.
    """
    raw = (row.get("attempts") or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return 0 if row.get("status") == PENDING else 1


def order_queue(queue: "list[dict[str, Any]]") -> "list[dict[str, Any]]":
    """Least-tried first; ties keep the order they already had.

    This is the whole scheduling rule, and it is applied to the file itself
    rather than only to a run's iteration order. A track that gets tried and
    stays unresolved sinks below everything tried fewer times, so successive
    runs rotate through the backlog instead of re-attacking the same head of
    the list every time. Sorting is stable, so within one attempt count the
    library's own order survives.
    """
    return sorted(queue, key=attempts)


def track_key(row: "dict[str, Any]") -> str:
    """Identify an export row against the log.

    Only needed when the queue is being rebuilt -- the rest of the time a
    line's presence in the queue is the whole of its state. persistent_id is
    iTunes' own stable identifier; title and artist are the fallback for
    hand-made CSVs that lack one.
    """
    persistent_id = (row.get("persistent_id") or "").strip()
    if persistent_id:
        return "id:" + persistent_id
    return "ta:{}|{}".format(
        (row.get("title") or "").strip().casefold(),
        (row.get("artist") or "").strip().casefold(),
    )


def build_queue(
    rows: "list[dict[str, Any]]",
    queue_path: "Union[str, Path]",
    log_path: "Union[str, Path]",
    restart: bool = False,
) -> "list[dict[str, Any]]":
    """Load the work list, creating it from the export if it is not there.

    Tracks already in the log are left out: they are in Spotify, so they are
    not work. That is what keeps ``--restart`` from importing everything a
    second time.
    """
    existing = [] if restart else read_queue(queue_path)
    if existing:
        return existing
    done = {track_key(r) for r in read_log(log_path)}
    fresh = [queue_row(r) for r in rows if track_key(r) not in done]
    _write(queue_path, QUEUE_FIELDS, fresh)
    return fresh


def playlist_from_log(log: "list[dict[str, Any]]") -> str:
    """The playlist previous runs were filling, so this one tops it up."""
    for row in reversed(log):
        playlist_id = (row.get("added_to") or "").strip()
        if playlist_id:
            return playlist_id
    return ""


def _record_match(item: "dict[str, Any]", match) -> None:
    candidate = match.candidate or {}
    item["status"] = match.status
    item["score"] = "" if match.score is None else "{:.3f}".format(match.score.total)
    # The URI is kept on rejected rows too: it is what lets spotify-resolve
    # accept a near miss without repeating the searches that found it.
    # Delivery is gated on status *and* URI, so a rejected row with a URI
    # cannot reach the playlist by accident.
    item["spotify_uri"] = candidate.get("uri", "")
    item["spotify_title"] = candidate.get("name", "")
    item["spotify_artist"] = ", ".join(candidate_artists(candidate))
    item["spotify_album"] = (candidate.get("album") or {}).get("name", "")
    item["spotify_duration_ms"] = str(candidate.get("duration_ms") or "")


def run_import(
    rows: "list[dict[str, Any]]",
    client,
    queue_path: "Union[str, Path]",
    log_path: "Union[str, Path]",
    name: "Optional[str]" = None,
    public: bool = False,
    dry_run: bool = False,
    min_score: float = DEFAULT_MIN_SCORE,
    progress: "Optional[Callable[[str], None]]" = None,
    on_track: "Optional[Callable[[int, int, dict[str, Any]], None]]" = None,
    restart: bool = False,
) -> "dict[str, Any]":
    """Drain the queue into a Spotify playlist.

    Matched tracks go out in batches of 100 -- Spotify's maximum per call --
    and only leave the queue for the log once Spotify has accepted them.
    Anything the run does not resolve simply stays queued for next time.
    """
    log = read_log(log_path)
    queue = build_queue(rows, queue_path, log_path, restart)
    playlist = {"id": "" if dry_run else playlist_from_log(log)}
    playlist["url"] = PLAYLIST_URL.format(playlist["id"]) if playlist["id"] else ""
    playlist_name = name or default_playlist_name()
    tally = {"added": 0, "searched": 0}
    # Identity, not equality: a queue row is removed by being the same object
    # that was just delivered.
    delivered: "list[dict[str, Any]]" = []

    def save_queue() -> None:
        # Rotating in place, not just on the way out: a run killed by a spent
        # quota still leaves the file in the order the next run should use.
        queue[:] = order_queue(queue)
        _write(queue_path, QUEUE_FIELDS, queue)

    def flush() -> None:
        """Deliver what has accumulated, then move those rows to the log.

        Delivering before recording is deliberate. Crashing between the two
        duplicates a track, which is visible in the playlist; recording first
        would retire tracks that never arrived, and no later run would notice.
        """
        if not delivered or dry_run:
            return
        if not playlist["id"]:
            # POST /me/playlists infers the owner from the token, so there is
            # no need to look the current user up first.
            created = client.create_playlist(
                playlist_name, public, "Imported from an iTunes library export."
            )
            playlist["id"] = created["id"]
            playlist["url"] = created.get("external_urls", {}).get("spotify", "") or (
                PLAYLIST_URL.format(created["id"])
            )
            if progress:
                progress("Created playlist '{}'".format(playlist_name))
        client.add_tracks(playlist["id"], [r["spotify_uri"] for r in delivered])
        for item in delivered:
            item["added_to"] = playlist["id"]
        _append(log_path, LOG_FIELDS, delivered)
        # Only now do they leave the work list.
        done = {id(r) for r in delivered}
        queue[:] = [r for r in queue if id(r) not in done]
        tally["added"] += len(delivered)
        delivered.clear()
        save_queue()
        if progress:
            progress("{} tracks in the playlist, {} still queued".format(
                tally["added"], len(queue)))

    try:
        # Matches carried over from an interrupted run are delivered before
        # any more quota is spent on searching.
        delivered.extend(r for r in queue if r["status"] == "matched" and r["spotify_uri"])
        flush()

        # Least-tried first. Spotify's budget is spent per request, not per
        # track, and re-examining a known near miss costs the same
        # searches as a track nobody has looked at yet -- but only one of the
        # two can put something in the playlist. On a resume after a spent
        # quota that ordering is the difference between progress and paying
        # full price to reconfirm verdicts already recorded.
        todo = order_queue(
            [r for r in queue if r["status"] not in ("matched", UNAVAILABLE)]
        )
        for count, item in enumerate(todo, start=1):
            # Counted before the search, and before _record_match overwrites
            # the status the legacy inference reads.
            spent = attempts(item) + 1
            _record_match(item, find_match(item, client, min_score))
            item["attempts"] = str(spent)
            tally["searched"] += 1
            if on_track:
                on_track(count, len(todo), item)
            if item["status"] == "matched" and item["spotify_uri"]:
                delivered.append(item)
            if len(delivered) >= ADD_TRACKS_BATCH:
                flush()
            elif count % 25 == 0:
                save_queue()  # checkpoint the matching work itself
        flush()

        return {
            "total": len(rows),
            "searched": tally["searched"],
            "added": tally["added"],
            "in_playlist": len(read_log(log_path)) if not dry_run else 0,
            "queued": len(queue),
            "rejected": sum(1 for r in queue if r["status"] == "rejected"),
            "not_found": sum(1 for r in queue if r["status"] == "not_found"),
            "unavailable": sum(1 for r in queue if r["status"] == UNAVAILABLE),
            "playlist_url": playlist["url"],
            "dry_run": dry_run,
        }
    finally:
        save_queue()
