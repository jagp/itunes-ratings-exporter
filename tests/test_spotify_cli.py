from pathlib import Path

from itunes_ratings_exporter.cli import main, spotify_import_main, spotify_resolve_main
from itunes_ratings_exporter.spotify.importer import (
    default_log_path,
    default_queue_path,
    read_log,
    read_queue,
)

from test_spotify_importer import FakeClient, full_catalogue, spotify_track, write_csv

FIXTURE = Path(__file__).parent / "fixtures" / "library.xml"


def test_export_still_works_without_a_subcommand(tmp_path):
    # The tool shipped with a flat interface; adding subcommands must not
    # break the documented invocation.
    out = tmp_path / "export"
    assert main(["--library", str(FIXTURE), "--out", str(out)]) == 0
    assert (out / "rated.csv").is_file()


def test_spotify_import_creates_a_playlist_and_a_log(tmp_path, capsys):
    csv_path = write_csv(tmp_path)
    client = FakeClient(full_catalogue())
    code = spotify_import_main(
        ["--csv", str(csv_path), "--min-stars", "0", "--name", "Mine"], client=client
    )
    assert code == 0
    assert client.created[0]["name"] == "Mine"
    # State lands beside the input CSV.
    assert default_log_path(csv_path).is_file()
    assert len(read_log(default_log_path(csv_path))) == 3
    out = capsys.readouterr().out
    assert "Added 3 tracks" in out
    assert "Queue empty" in out


def test_min_stars_filters_before_matching(tmp_path):
    csv_path = write_csv(tmp_path)
    client = FakeClient(full_catalogue())
    assert spotify_import_main(["--csv", str(csv_path), "--min-stars", "5"], client=client) == 0
    assert client.added == ["spotify:track:kp"]


def test_dry_run_reports_without_creating_anything(tmp_path, capsys):
    csv_path = write_csv(tmp_path)
    client = FakeClient(full_catalogue())
    code = spotify_import_main(
        ["--csv", str(csv_path), "--min-stars", "0", "--dry-run"], client=client
    )
    assert code == 0
    assert client.created == []
    assert "Dry run" in capsys.readouterr().out


def test_missing_csv_exits_four_and_points_at_the_exporter(tmp_path, capsys):
    code = spotify_import_main(["--csv", str(tmp_path / "nope.csv")], client=FakeClient())
    assert code == 4
    assert "Run the exporter first" in capsys.readouterr().err


def test_malformed_csv_exits_four(tmp_path, capsys):
    csv_path = write_csv(tmp_path, "title,album\nSong,Album\n")
    assert spotify_import_main(["--csv", str(csv_path)], client=FakeClient()) == 4
    assert "artist" in capsys.readouterr().err


def test_missing_client_id_exits_five_with_setup_help(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    csv_path = write_csv(tmp_path)
    # No client injected, so the command must authorize -- and cannot.
    assert spotify_import_main(["--csv", str(csv_path)]) == 5
    assert "developer.spotify.com" in capsys.readouterr().err


def test_api_failure_exits_six_and_says_where_progress_went(tmp_path, capsys):
    csv_path = write_csv(tmp_path)
    client = FakeClient(full_catalogue(), fail_on_add=True)
    assert spotify_import_main(["--csv", str(csv_path)], client=client) == 6
    assert "Progress was saved" in capsys.readouterr().err


def test_nothing_above_the_star_threshold_is_not_an_error(tmp_path, capsys):
    csv_path = write_csv(
        tmp_path, "title,artist,rating_stars,duration_ms\nCreep,Radiohead,3,238000\n"
    )
    client = FakeClient(full_catalogue())
    assert spotify_import_main(["--csv", str(csv_path), "--min-stars", "5"], client=client) == 0
    assert "Nothing to import" in capsys.readouterr().out
    assert client.created == []


def test_every_track_is_logged_as_it_is_decided(tmp_path, capsys):
    # If a run dies partway, this scrollback is the only record of which
    # tracks were resolved -- so it names each one, not every twenty-fifth.
    csv_path = write_csv(tmp_path)
    catalogue = full_catalogue()
    del catalogue["Idioteque"]
    spotify_import_main(
        ["--csv", str(csv_path), "--min-stars", "0"], client=FakeClient(catalogue)
    )
    out = capsys.readouterr().out
    assert "[   1/3] match" in out
    assert "Karma Police -- Radiohead" in out
    assert "->  Karma Police -- Radiohead" in out
    assert "[   3/3] MISS" in out


def test_quiet_suppresses_the_per_track_log_but_not_the_totals(tmp_path, capsys):
    csv_path = write_csv(tmp_path)
    spotify_import_main(
        ["--csv", str(csv_path), "--min-stars", "0", "--quiet"],
        client=FakeClient(full_catalogue()),
    )
    out = capsys.readouterr().out
    assert "[   1/3]" not in out
    assert "The playlist now holds 3 of 3 tracks" in out


def test_re_running_continues_from_the_queue_with_no_extra_flag(tmp_path, capsys):
    csv_path = write_csv(tmp_path)
    catalogue = full_catalogue()
    del catalogue["Idioteque"]
    spotify_import_main(["--csv", str(csv_path), "--min-stars", "0"], client=FakeClient(catalogue))
    capsys.readouterr()

    second = FakeClient(full_catalogue())
    code = spotify_import_main(["--csv", str(csv_path), "--min-stars", "0"], client=second)
    assert code == 0
    # The two already delivered are not searched or added again.
    assert second.added == ["spotify:track:id"]
    assert second.created == []
    out = capsys.readouterr().out
    assert "Continuing: 1 tracks still queued" in out
    assert "Queue empty" in out


def test_an_unfinished_run_says_what_is_left(tmp_path, capsys):
    csv_path = write_csv(tmp_path)
    catalogue = full_catalogue()
    del catalogue["Idioteque"]
    spotify_import_main(["--csv", str(csv_path), "--min-stars", "0"], client=FakeClient(catalogue))
    out = capsys.readouterr().out
    assert "The playlist now holds 2 of 3 tracks; 1 still queued" in out
    assert "Re-run the same command to continue." in out


def test_restart_requeues_without_reimporting_what_is_already_in(tmp_path, capsys):
    csv_path = write_csv(tmp_path)
    catalogue = full_catalogue()
    del catalogue["Idioteque"]
    spotify_import_main(["--csv", str(csv_path), "--min-stars", "0"], client=FakeClient(catalogue))
    capsys.readouterr()

    second = FakeClient(full_catalogue())
    code = spotify_import_main(
        ["--csv", str(csv_path), "--min-stars", "0", "--restart"], client=second
    )
    assert code == 0
    assert "Rebuilding the queue" in capsys.readouterr().out
    # Only the track that never made it is re-queued and imported.
    assert second.added == ["spotify:track:id"]
    assert read_queue(default_queue_path(csv_path)) == []


def test_a_spent_quota_exits_seven_and_says_when_to_come_back(tmp_path, capsys):
    from itunes_ratings_exporter.spotify.client import SpotifyQuotaError

    class QuotaClient(FakeClient):
        def search_tracks(self, query, limit=5):
            raise SpotifyQuotaError(10148.0, "request quota exhausted; come back in 2.8 hours.")

    csv_path = write_csv(tmp_path)
    # Exit 7, distinct from 6, so a wrapper can tell "wait and retry" apart
    # from "this run is broken".
    assert spotify_import_main(["--csv", str(csv_path)], client=QuotaClient()) == 7
    err = capsys.readouterr().err
    assert "2.8 hours" in err
    assert "holds what is left" in err
    assert "Re-run the same command" in err


def test_main_routes_the_subcommand(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    csv_path = write_csv(tmp_path)
    # Reaching the auth failure proves the subcommand was routed and parsed.
    assert main(["spotify-import", "--csv", str(csv_path)]) == 5


# --- spotify-resolve ----------------------------------------------------


def test_resolve_without_a_queue_says_so(tmp_path, capsys):
    code = spotify_resolve_main(["--csv", str(tmp_path / "rated.csv")])
    assert code == 0
    assert "Run 'spotify-import' first" in capsys.readouterr().out


def test_resolve_accepts_a_near_miss_and_import_delivers_it(
    tmp_path, capsys, monkeypatch
):
    """The full loop: import rejects, resolve accepts, re-import delivers."""
    csv_path = write_csv(tmp_path)
    catalogue = full_catalogue()
    # Right song, wrong artist credit: rejected, but with the URI recorded.
    catalogue["Creep"] = spotify_track(
        "Creep", "Stone Temple Pilots", 238_000, "spotify:track:cr"
    )
    spotify_import_main(
        ["--csv", str(csv_path), "--min-stars", "0"], client=FakeClient(catalogue)
    )
    answers = iter(["a"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    code = spotify_resolve_main(["--csv", str(csv_path)], client=FakeClient())
    assert code == 0
    assert "1 accepted" in capsys.readouterr().out
    resumed = FakeClient(catalogue)
    spotify_import_main(["--csv", str(csv_path), "--min-stars", "0"], client=resumed)
    assert "spotify:track:cr" in resumed.added
    assert read_queue(default_queue_path(csv_path)) == []


def test_resolve_dispatches_from_main(tmp_path, capsys):
    assert main(["spotify-resolve", "--csv", str(tmp_path / "rated.csv")]) == 0
    assert "resolve" in capsys.readouterr().out
