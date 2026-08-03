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
        # Matching spends up to three searches on a track and stops at the
        # first one that lands, so this is an upper bound, not a promise.
        pending = outstanding or len(rows)
        print(
            "Paced at {:g} requests/second -- up to about {:.0f} minutes. "
            "A spent quota costs hours, so this errs slow.".format(
                args.rate, pending * 3.0 / args.rate / 60.0
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
            f"{exc}\n{queue_path} holds what is left; tracks already in the "
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

    where = summary["playlist_url"] or "your playlist"
    if summary["dry_run"]:
        print(f"Dry run: nothing was added. Searched {summary['searched']} tracks.")
        return 0

    if summary["added"]:
        print(f"Added {summary['added']} tracks to {where}")
    print(
        "The playlist now holds {in_playlist} of {total} tracks; "
        "{queued} still queued ({rejected} near misses, "
        "{not_found} not found).".format(**summary)
    )
    if summary["queued"]:
        # The queue is the durable to-do list, so the next step is always the
        # same command -- no flag, no bookkeeping.
        print(f"Still to do: {queue_path}")
        print("Re-run the same command to continue.")
    else:
        print(f"Queue empty -- the import is complete. Log: {log_path}")
    return 0
