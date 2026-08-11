"""Command-line interface for the iTunes ratings exporter."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .exporters import (
    manually_rated,
    write_library_json,
    write_playlists_csv,
    write_rated_csv,
    write_tracks_csv,
)
from .parser import parse_library

_ENABLE_XML_HELP = (
    "No iTunes library XML found at: {path}\n"
    "In iTunes, open Edit > Preferences > Advanced and enable\n"
    '"Share iTunes Library XML with other applications", then try again.\n'
    "Or pass the file's location explicitly with --library."
)

SPOTIFY_IMPORT = "spotify-import"
SPOTIFY_RESOLVE = "spotify-resolve"


def default_library_path() -> Path:
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / "Music" / "iTunes" / "iTunes Music Library.xml"


def main(argv: "list[str] | None" = None) -> int:
    """Dispatch to a subcommand, or run the export when none is named.

    The export interface shipped without subcommands, so bare
    ``--library``/``--out`` invocations must keep working. Only an explicit
    subcommand name in first position routes elsewhere.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == SPOTIFY_IMPORT:
        return spotify_import_main(args[1:])
    if args and args[0] == SPOTIFY_RESOLVE:
        return spotify_resolve_main(args[1:])
    return export_main(args)


def export_main(argv: "list[str]") -> int:
    ap = argparse.ArgumentParser(
        prog="itunes-ratings-exporter",
        description="Export ratings, play counts, and playlists from "
        "iTunes Music Library.xml to CSV/JSON.",
        epilog="Run '{} --help' to push an export into Spotify.".format(SPOTIFY_IMPORT),
    )
    ap.add_argument("--library", help="Path to iTunes Music Library.xml")
    ap.add_argument("--out", default="export", help="Output directory (default: ./export)")
    args = ap.parse_args(argv)

    library = Path(args.library) if args.library else default_library_path()
    if not library.is_file():
        print(_ENABLE_XML_HELP.format(path=library), file=sys.stderr)
        return 2

    try:
        lib = parse_library(library)
    except Exception as exc:  # plistlib raises several types for bad input
        print(f"Could not parse {library}: {exc}", file=sys.stderr)
        return 1

    tracks, playlists = lib["tracks"], lib["playlists"]
    out = Path(args.out)
    try:
        out.mkdir(parents=True, exist_ok=True)
        write_tracks_csv(tracks, out / "tracks.csv")
        write_rated_csv(tracks, out / "rated.csv")
        write_playlists_csv(playlists, tracks, out / "playlists.csv")
        write_library_json(tracks, playlists, str(library), out / "library.json")
    except OSError as exc:
        print(f"Could not write to output directory {out}: {exc}", file=sys.stderr)
        return 3

    rated = manually_rated(tracks)
    played = [t for t in tracks if t["play_count"] is not None]
    print(
        f"Exported {len(tracks)} tracks ({len(rated)} manually rated, "
        f"{len(played)} with plays), {len(playlists)} playlists -> {out}"
    )
    return 0


def _spotify_import_parser() -> argparse.ArgumentParser:
    from .spotify.client import DEFAULT_REQUESTS_PER_SECOND
    from .spotify.matcher import DEFAULT_MIN_SCORE

    ap = argparse.ArgumentParser(
        prog="itunes-ratings-exporter " + SPOTIFY_IMPORT,
        description="Create a Spotify playlist from an exported ratings CSV.",
    )
    ap.add_argument("--csv", default=str(Path("export") / "rated.csv"), help="Input CSV")
    ap.add_argument("--name", help="Playlist name (default: iTunes Ratings <today>)")
    ap.add_argument("--min-stars", type=int, default=4, help="Minimum star rating (default: 4)")
    ap.add_argument("--limit", type=int, help="Import at most this many tracks")
    ap.add_argument("--public", action="store_true", help="Make the playlist public")
    ap.add_argument("--dry-run", action="store_true", help="Match and report, change nothing")
    ap.add_argument(
        "--min-score",
        type=float,
        default=DEFAULT_MIN_SCORE,
        help="Match acceptance threshold, 0.0-1.0 (default: %(default)s)",
    )
    ap.add_argument(
        "--client-id",
        default=os.environ.get("SPOTIFY_CLIENT_ID", ""),
        help="Spotify app client ID (default: $SPOTIFY_CLIENT_ID)",
    )
    ap.add_argument(
        "--restart",
        action="store_true",
        help="Rebuild the work queue from the input CSV, discarding the "
        "matching done so far. The log of tracks already in the playlist is "
        "kept, so they are not imported twice.",
    )
    ap.add_argument(
        "--rate",
        type=float,
        default=DEFAULT_REQUESTS_PER_SECOND,
        help="Requests per second to Spotify (default: %(default)s). Lower is "
        "safer on a large library: a spent rolling quota locks the account "
        "out for hours, while pacing costs minutes. 0 disables pacing.",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Only print progress totals, not every track",
    )
    return ap


_STATUS_LABEL = {"matched": "match ", "rejected": "REJECT", "not_found": "MISS  "}


def _print_track(count: int, total: int, result: "dict[str, str]") -> None:
    """Log one track as it is decided.

    Printed for every track, not just every 25th, so that a run cut short by
    a quota or a crash still leaves a scrollback record of exactly which
    tracks were resolved and which were not.
    """
    status = result.get("status", "")
    line = "[{:>4}/{}] {} {:<7} {} -- {}".format(
        count,
        total,
        _STATUS_LABEL.get(status, status),
        result.get("score", ""),
        result.get("title", ""),
        result.get("artist", ""),
    )
    if status == "matched":
        line += "  ->  {} -- {}".format(
            result.get("spotify_title", ""), result.get("spotify_artist", "")
        )
    elif status == "rejected":
        line += "  (near miss: {} -- {})".format(
            result.get("spotify_title", ""), result.get("spotify_artist", "")
        )
    print(line, flush=True)


def _request_cost(client, rate: float) -> str:
    """Report what the run spent, paired with the pace it spent it at.

    Whether the ceiling that stops a large import is a fixed request budget or
    a rate limit is still open, and the two predict different things about
    this pair: a budget caps the count whatever the pace, while a rate limit
    lets a slower run reach a higher count. Printing both together is what
    makes consecutive runs comparable instead of anecdotal.
    """
    made = getattr(client, "requests_made", None)
    if not made:
        return ""
    pace = "unpaced" if rate <= 0 else "{:g}/s".format(rate)
    return "Spent {} Spotify requests this run (paced at {}).".format(made, pace)


def spotify_import_main(argv: "list[str]", client=None) -> int:
    """Match an exported CSV against Spotify and build a playlist.

    ``client`` exists so tests can drive the whole command with a fake API;
    normal runs authorize and construct a real one.
    """
    from .spotify.auth import AuthError, get_access_token
    from .spotify.client import SpotifyApiError, SpotifyClient, SpotifyQuotaError
    from .spotify.importer import (
        ImportInputError,
        default_log_path,
        default_playlist_name,
        default_queue_path,
        read_queue,
        read_rows,
        run_import,
    )

    args = _spotify_import_parser().parse_args(argv)

    try:
        rows = read_rows(args.csv, args.min_stars, args.limit)
    except ImportInputError as exc:
        print(str(exc), file=sys.stderr)
        return 4

    if not rows:
        print(f"No tracks in {args.csv} rated {args.min_stars}+ stars. Nothing to import.")
        return 0

    queue_path = default_queue_path(args.csv)
    log_path = default_log_path(args.csv)

    if client is None:
        # A dry run still needs search access to score candidates; it simply
        # never writes anything back to the account.
        try:
            client = SpotifyClient(
                get_access_token(args.client_id),
                announce=lambda msg: print(msg, file=sys.stderr, flush=True),
                requests_per_second=args.rate,
            )
        except AuthError as exc:
            print(str(exc), file=sys.stderr)
            return 5

    try:
        outstanding = len(read_queue(queue_path))
    except ImportInputError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    if args.restart:
        print(f"Rebuilding the queue from {args.csv}.")
    elif outstanding:
        print(f"Continuing: {outstanding} tracks still queued in {queue_path}")

    print(f"Matching {len(rows)} tracks against Spotify...")
    if args.rate > 0:
        # Matching stops at the first query that lands, but an unresolved
        # track can now spend extra searches on alternate readings of its
        # metadata (matcher.py row_interpretations), so budget four per
        # track as a working upper bound, not a promise.
        pending = outstanding or len(rows)
        print(
            "Paced at {:g} requests/second -- up to about {:.0f} minutes. "
            "A spent quota costs hours, so this errs slow.".format(
                args.rate, pending * 4.0 / args.rate / 60.0
            )
        )
    try:
        summary = run_import(
            rows,
            client,
            queue_path,
            log_path,
            name=args.name or default_playlist_name(),
            public=args.public,
            dry_run=args.dry_run,
            min_score=args.min_score,
            progress=lambda msg: print(msg, flush=True),
            on_track=None if args.quiet else _print_track,
            restart=args.restart,
        )
    except SpotifyQuotaError as exc:
        print(
            f"{exc}\n{_request_cost(client, args.rate)}\n"
            f"{queue_path} holds what is left; tracks already in the "
            f"playlist have moved to {log_path}.\nRe-run the same command to "
            f"carry on -- it picks up from the queue.",
            file=sys.stderr,
        )
        return 7
    except SpotifyApiError as exc:
        print(f"{exc}\nProgress was saved to {queue_path}.", file=sys.stderr)
        return 6
    except OSError as exc:
        print(f"Could not write to {queue_path}: {exc}", file=sys.stderr)
        return 3

    cost = _request_cost(client, args.rate)
    if cost:
        print(cost)

    where = summary["playlist_url"] or "your playlist"
    if summary["dry_run"]:
        print(f"Dry run: nothing was added. Searched {summary['searched']} tracks.")
        return 0

    if summary["added"]:
        print(f"Added {summary['added']} tracks to {where}")
    breakdown = "{rejected} near misses, {not_found} not found".format(**summary)
    if summary.get("unavailable"):
        breakdown += ", {unavailable} marked unavailable".format(**summary)
    if summary.get("local"):
        breakdown += ", {local} locally managed".format(**summary)
    print(
        "The playlist now holds {in_playlist} of {total} tracks; "
        "{queued} still queued ({breakdown}).".format(breakdown=breakdown, **summary)
    )
    # Settled rows (unavailable, locally managed) keep their queue line but
    # are not work, so completion is "nothing workable", not "file empty".
    workable = summary["queued"] - summary.get("unavailable", 0) - summary.get("local", 0)
    if workable:
        # The queue is the durable to-do list, so the next step is always the
        # same command -- no flag, no bookkeeping.
        print(f"Still to do: {queue_path}")
        print("Re-run the same command to continue.")
    elif summary["queued"]:
        print(
            "Every remaining track is settled (unavailable or locally "
            f"managed) -- the import is complete. Log: {log_path}"
        )
    else:
        print(f"Queue empty -- the import is complete. Log: {log_path}")
    return 0


def _spotify_resolve_parser() -> argparse.ArgumentParser:
    from .spotify.client import DEFAULT_REQUESTS_PER_SECOND

    ap = argparse.ArgumentParser(
        prog="itunes-ratings-exporter " + SPOTIFY_RESOLVE,
        description="Decide the tracks spotify-import could not place: accept "
        "near misses, pick from fresh candidates, or mark tracks unavailable. "
        "Only fetching fresh candidates talks to Spotify.",
    )
    ap.add_argument(
        "--csv",
        default=str(Path("export") / "rated.csv"),
        help="The import's input CSV; the queue lives beside it",
    )
    ap.add_argument(
        "--client-id",
        default=os.environ.get("SPOTIFY_CLIENT_ID", ""),
        help="Spotify app client ID (default: $SPOTIFY_CLIENT_ID). Only "
        "needed if you ask for fresh candidates.",
    )
    ap.add_argument(
        "--rate",
        type=float,
        default=DEFAULT_REQUESTS_PER_SECOND,
        help="Requests per second when fetching candidates (default: %(default)s)",
    )
    ap.add_argument(
        "--include-unavailable",
        action="store_true",
        help="Also revisit settled tracks (marked unavailable or locally managed)",
    )
    return ap


def spotify_resolve_main(argv: "list[str]", client=None) -> int:
    """Walk the unresolved queue rows and record the user's decisions.

    ``client`` exists so tests can inject a fake API. Normal runs authorize
    lazily -- the first time the user asks for fresh candidates -- because
    every other action works from what the queue already recorded.
    """
    from .spotify.auth import get_access_token
    from .spotify.client import SpotifyClient
    from .spotify.importer import ImportInputError, default_queue_path, read_queue
    from .spotify.resolver import resolvable, run_resolve

    args = _spotify_resolve_parser().parse_args(argv)
    queue_path = default_queue_path(args.csv)

    try:
        queue = read_queue(queue_path)
    except ImportInputError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    if not queue:
        print(
            f"No queue at {queue_path}. Run '{SPOTIFY_IMPORT}' first; resolve "
            "works on what it leaves behind."
        )
        return 0

    todo = resolvable(queue, args.include_unavailable)
    if not todo:
        print(
            "Nothing to resolve -- every queued track is matched, pending, or "
            "settled (revisit settled verdicts with --include-unavailable)."
        )
        return 0

    def get_client():
        if client is not None:
            return client
        return SpotifyClient(
            get_access_token(args.client_id),
            announce=lambda msg: print(msg, file=sys.stderr, flush=True),
            requests_per_second=args.rate,
        )

    try:
        tally = run_resolve(
            queue,
            queue_path,
            get_client,
            include_unavailable=args.include_unavailable,
        )
    except OSError as exc:
        print(f"Could not write to {queue_path}: {exc}", file=sys.stderr)
        return 3
    except (KeyboardInterrupt, EOFError):
        # Decisions are saved as they are made, so a ^C loses nothing.
        print("\nStopped. Everything decided so far is saved in the queue.")
        return 0

    print(
        "\nResolved this session: {accepted} accepted, {unavailable} marked "
        "unavailable, {local} locally managed, {requeued} requeued, "
        "{kept} left as they were.".format(
            **{k: tally.get(k, 0)
               for k in ("accepted", "unavailable", "local", "requeued", "kept")}
        )
    )
    if tally.get("accepted"):
        print(
            "Accepted tracks are queued as matches -- run '{}' to add them "
            "to the playlist.".format(SPOTIFY_IMPORT)
        )
    return 0
