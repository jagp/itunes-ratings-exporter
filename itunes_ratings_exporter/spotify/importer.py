"""Turn an exported iTunes CSV into a Spotify playlist."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional, Union

from .matcher import DEFAULT_MIN_SCORE, candidate_artists, find_match

REQUIRED_COLUMNS = ("title", "artist")

REPORT_FIELDS = [
    # persistent_id leads so a resume can key on it: iTunes titles are not
    # unique, but this identifier is stable across exports.
    "persistent_id",
    "title",
    "artist",
    "album",
    "rating_stars",
    "status",
    "score",
    "spotify_uri",
    "spotify_title",
    "spotify_artist",
]

# Statuses a resume treats as settled. not_found is deliberately absent: a
# miss can be a transient search failure, so those are retried -- but only
# after every unattempted track has had its turn.
_SETTLED = ("matched", "rejected")


class ImportInputError(RuntimeError):
    """The input CSV is missing, unreadable, or the wrong shape."""


def default_playlist_name(today: "Optional[date]" = None) -> str:
    return "iTunes Ratings {}".format((today or date.today()).isoformat())


def read_rows(
    csv_path: "Union[str, Path]", min_stars: int = 0, limit: "Optional[int]" = None
) -> "list[dict[str, Any]]":
    """Load the CSV, keeping only tracks rated at least ``min_stars``."""
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


def write_report(path: "Union[str, Path]", results: "list[dict[str, Any]]") -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerow({k: result.get(k, "") for k in REPORT_FIELDS})


def row_key(row: "dict[str, Any]") -> str:
    """Identify a track across runs.

    persistent_id is iTunes' own stable identifier; the title/artist pair is
    a fallback for CSVs that predate it or were hand-assembled.
    """
    persistent_id = (row.get("persistent_id") or "").strip()
    if persistent_id:
        return "id:" + persistent_id
    return "ta:{}|{}".format(
        (row.get("title") or "").strip().casefold(),
        (row.get("artist") or "").strip().casefold(),
    )


def read_prior_report(path: "Union[str, Path]") -> "dict[str, dict[str, Any]]":
    """Load a previous run's report, keyed for resume. Absent file -> {}."""
    report = Path(path)
    if not report.is_file():
        return {}
    try:
        with open(report, newline="", encoding="utf-8") as f:
            return {row_key(r): r for r in csv.DictReader(f)}
    except OSError as exc:
        raise ImportInputError("Could not read the report at {}: {}".format(report, exc))


def _result_row(row: "dict[str, Any]", match) -> "dict[str, Any]":
    candidate = match.candidate or {}
    return {
        "persistent_id": row.get("persistent_id", ""),
        "title": row.get("title", ""),
        "artist": row.get("artist", ""),
        "album": row.get("album", ""),
        "rating_stars": row.get("rating_stars", ""),
        "status": match.status,
        "score": "" if match.score is None else "{:.3f}".format(match.score.total),
        "spotify_uri": candidate.get("uri", "") if match.status == "matched" else "",
        "spotify_title": candidate.get("name", ""),
        "spotify_artist": ", ".join(candidate_artists(candidate)),
    }


def _plan(
    rows: "list[dict[str, Any]]", prior: "dict[str, dict[str, Any]]"
) -> "tuple[dict[int, dict[str, Any]], list[tuple[int, dict[str, Any]]]]":
    """Split rows into results already settled and work still to do.

    Returns the reusable results keyed by their position, and the rows to
    search -- unattempted ones first, previously not_found ones last.
    """
    settled: "dict[int, dict[str, Any]]" = {}
    fresh: "list[tuple[int, dict[str, Any]]]" = []
    retry: "list[tuple[int, dict[str, Any]]]" = []
    for index, row in enumerate(rows):
        previous = prior.get(row_key(row))
        if previous and previous.get("status") in _SETTLED:
            settled[index] = dict(previous)
        elif previous and previous.get("status") == "not_found":
            retry.append((index, row))
        else:
            fresh.append((index, row))
    return settled, fresh + retry


def run_import(
    rows: "list[dict[str, Any]]",
    client,
    report_path: "Union[str, Path]",
    name: "Optional[str]" = None,
    public: bool = False,
    dry_run: bool = False,
    min_score: float = DEFAULT_MIN_SCORE,
    progress: "Optional[Callable[[str], None]]" = None,
    on_track: "Optional[Callable[[int, int, dict[str, Any]], None]]" = None,
    prior: "Optional[dict[str, dict[str, Any]]]" = None,
) -> "dict[str, Any]":
    """Match every row, create the playlist, and write the report.

    The report is written even when the API fails partway through, so the
    matching work -- the slow part -- is never lost to a network blip. Pass
    ``prior`` (from :func:`read_prior_report`) to reuse settled results from
    an earlier run instead of spending quota on them again.
    """
    settled, todo = _plan(rows, prior or {})
    # Results are keyed by position so the report keeps the input's order no
    # matter what order the work actually happened in.
    results: "dict[int, dict[str, Any]]" = dict(settled)

    def ordered_results() -> "list[dict[str, Any]]":
        return [results[i] for i in sorted(results)]

    try:
        for count, (index, row) in enumerate(todo, start=1):
            match = find_match(row, client, min_score)
            results[index] = _result_row(row, match)
            if on_track:
                on_track(count, len(todo), results[index])
            if progress and (count % 25 == 0 or count == len(todo)):
                found = sum(1 for r in results.values() if r["status"] == "matched")
                progress("Matched {}/{} tracks ({} found)".format(count, len(todo), found))

        finished = ordered_results()
        uris = [r["spotify_uri"] for r in finished if r["status"] == "matched" and r["spotify_uri"]]
        summary = {
            "total": len(rows),
            "reused": len(settled),
            "searched": len(todo),
            "matched": len(uris),
            "rejected": sum(1 for r in finished if r["status"] == "rejected"),
            "not_found": sum(1 for r in finished if r["status"] == "not_found"),
            "playlist_url": "",
            "added": 0,
            "dry_run": dry_run,
        }
        if uris and not dry_run:
            # POST /me/playlists infers the owner from the token, so there is
            # no longer any reason to look the current user up first.
            playlist = client.create_playlist(
                name or default_playlist_name(),
                public,
                "Imported from an iTunes library export.",
            )
            if progress:
                progress("Adding {} tracks to '{}'...".format(len(uris), name or "playlist"))
            summary["added"] = client.add_tracks(playlist["id"], uris)
            summary["playlist_url"] = playlist.get("external_urls", {}).get("spotify", "")
        return summary
    finally:
        write_report(report_path, ordered_results())
