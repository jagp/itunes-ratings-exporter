from pathlib import Path

from itunes_ratings_exporter.cli import main, spotify_import_main

from test_spotify_importer import FakeClient, full_catalogue, write_csv

FIXTURE = Path(__file__).parent / "fixtures" / "library.xml"


def test_export_still_works_without_a_subcommand(tmp_path):
    # The tool shipped with a flat interface; adding subcommands must not
    # break the documented invocation.
    out = tmp_path / "export"
    assert main(["--library", str(FIXTURE), "--out", str(out)]) == 0
    assert (out / "rated.csv").is_file()


def test_spotify_import_saves_to_liked_songs_and_writes_a_report(tmp_path, capsys):
    csv_path = write_csv(tmp_path)
    client = FakeClient(full_catalogue())
    code = spotify_import_main(["--csv", str(csv_path), "--min-stars", "0"], client=client)
    assert code == 0
    assert (tmp_path / "spotify_import_report.csv").is_file()
    assert client.saved == ["kp", "cr", "id"]
    out = capsys.readouterr().out
    assert "Matched 3/3" in out
    assert "Added 3 tracks" in out


def test_min_stars_filters_before_matching(tmp_path):
    csv_path = write_csv(tmp_path)
    client = FakeClient(full_catalogue())
    assert spotify_import_main(["--csv", str(csv_path), "--min-stars", "5"], client=client) == 0
    assert client.saved == ["kp"]


def test_dry_run_reports_without_creating_anything(tmp_path, capsys):
    csv_path = write_csv(tmp_path)
    client = FakeClient(full_catalogue())
    code = spotify_import_main(
        ["--csv", str(csv_path), "--min-stars", "0", "--dry-run"], client=client
    )
    assert code == 0
    assert client.saved == []
    assert "Dry run" in capsys.readouterr().out


def test_report_path_can_be_redirected(tmp_path):
    csv_path = write_csv(tmp_path)
    report = tmp_path / "custom" / "report.csv"
    report.parent.mkdir()
    spotify_import_main(
        ["--csv", str(csv_path), "--report", str(report)], client=FakeClient(full_catalogue())
    )
    assert report.is_file()


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


def test_api_failure_exits_six_and_says_where_the_report_went(tmp_path, capsys):
    csv_path = write_csv(tmp_path)
    client = FakeClient(full_catalogue(), fail_on_save=True)
    assert spotify_import_main(["--csv", str(csv_path)], client=client) == 6
    assert "Partial results were written" in capsys.readouterr().err


def test_nothing_above_the_star_threshold_is_not_an_error(tmp_path, capsys):
    csv_path = write_csv(
        tmp_path, "title,artist,rating_stars,duration_ms\nCreep,Radiohead,3,238000\n"
    )
    client = FakeClient(full_catalogue())
    assert spotify_import_main(["--csv", str(csv_path), "--min-stars", "5"], client=client) == 0
    assert "Nothing to import" in capsys.readouterr().out
    assert client.saved == []


def test_main_routes_the_subcommand(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    csv_path = write_csv(tmp_path)
    # Reaching the auth failure proves the subcommand was routed and parsed.
    assert main(["spotify-import", "--csv", str(csv_path)]) == 5
