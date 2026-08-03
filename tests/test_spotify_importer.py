import csv

import pytest

from itunes_ratings_exporter.spotify.client import SpotifyApiError
from itunes_ratings_exporter.spotify.importer import (
    ImportInputError,
    default_playlist_name,
    read_rows,
    run_import,
)

HEADER = "persistent_id,title,artist,album,rating_stars,duration_ms\n"
ROWS = (
    "1,Karma Police,Radiohead,OK Computer,5,263000\n"
    "2,Creep,Radiohead,Pablo Honey,3,238000\n"
    "3,Idioteque,Radiohead,Kid A,4,228000\n"
)


def write_csv(tmp_path, body=HEADER + ROWS, name="rated.csv"):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def spotify_track(name, artist, duration_ms, uri):
    return {
        "name": name,
        "artists": [{"name": artist}],
        "duration_ms": duration_ms,
        "uri": uri,
    }


class FakeClient:
    def __init__(self, catalogue=None, fail_on_add=False):
        self.catalogue = catalogue or {}
        self.fail_on_add = fail_on_add
        self.created = []
        self.added = []

    def search_tracks(self, query, limit=5):
        for title, result in self.catalogue.items():
            if title.lower() in query.lower():
                return [result]
        return []

    def current_user(self):
        return {"id": "user1"}

    def create_playlist(self, name, public=False, description=""):
        self.created.append({"name": name, "public": public})
        return {"id": "pl1", "external_urls": {"spotify": "https://open.spotify.com/pl1"}}

    def add_tracks(self, playlist_id, uris):
        if self.fail_on_add:
            raise SpotifyApiError(502, "upstream exploded")
        self.added.extend(uris)
        return len(uris)


def full_catalogue():
    return {
        "Karma Police": spotify_track("Karma Police", "Radiohead", 263_000, "spotify:track:kp"),
        "Creep": spotify_track("Creep", "Radiohead", 238_000, "spotify:track:cr"),
        "Idioteque": spotify_track("Idioteque", "Radiohead", 228_000, "spotify:track:id"),
    }


def read_report(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_read_rows_filters_by_minimum_stars(tmp_path):
    rows = read_rows(write_csv(tmp_path), min_stars=4)
    assert [r["title"] for r in rows] == ["Karma Police", "Idioteque"]


def test_read_rows_with_zero_stars_keeps_everything(tmp_path):
    assert len(read_rows(write_csv(tmp_path), min_stars=0)) == 3


def test_read_rows_applies_the_limit_after_filtering(tmp_path):
    rows = read_rows(write_csv(tmp_path), min_stars=4, limit=1)
    assert [r["title"] for r in rows] == ["Karma Police"]


def test_read_rows_names_the_missing_file(tmp_path):
    with pytest.raises(ImportInputError) as exc:
        read_rows(tmp_path / "absent.csv")
    assert "absent.csv" in str(exc.value)


def test_read_rows_names_missing_required_columns(tmp_path):
    path = write_csv(tmp_path, "title,album\nSong,Album\n")
    with pytest.raises(ImportInputError) as exc:
        read_rows(path)
    assert "artist" in str(exc.value)


def test_read_rows_explains_when_ratings_are_absent(tmp_path):
    path = write_csv(tmp_path, "title,artist\nSong,Band\n")
    with pytest.raises(ImportInputError) as exc:
        read_rows(path, min_stars=4)
    assert "--min-stars 0" in str(exc.value)


def test_unrated_rows_survive_when_no_filter_is_applied(tmp_path):
    path = write_csv(tmp_path, "title,artist\nSong,Band\n")
    assert len(read_rows(path, min_stars=0)) == 1


def test_run_import_creates_a_playlist_and_adds_every_match(tmp_path):
    rows = read_rows(write_csv(tmp_path), min_stars=0)
    client = FakeClient(full_catalogue())
    summary = run_import(rows, client, tmp_path / "report.csv", name="My Playlist")
    assert summary["matched"] == 3
    assert summary["added"] == 3
    assert summary["playlist_url"] == "https://open.spotify.com/pl1"
    assert client.created == [{"name": "My Playlist", "public": False}]
    assert client.added == ["spotify:track:kp", "spotify:track:cr", "spotify:track:id"]


def test_run_import_reports_every_status(tmp_path):
    rows = read_rows(write_csv(tmp_path), min_stars=0)
    catalogue = full_catalogue()
    # A wrong-artist near miss, and one track absent from the catalogue.
    catalogue["Creep"] = spotify_track("Creep", "Stone Temple Pilots", 238_000, "spotify:track:x")
    del catalogue["Idioteque"]
    summary = run_import(rows, FakeClient(catalogue), tmp_path / "report.csv")

    assert (summary["matched"], summary["rejected"], summary["not_found"]) == (1, 1, 1)
    by_title = {r["title"]: r for r in read_report(tmp_path / "report.csv")}
    assert by_title["Karma Police"]["status"] == "matched"
    assert by_title["Karma Police"]["spotify_uri"] == "spotify:track:kp"
    assert by_title["Creep"]["status"] == "rejected"
    # A rejected row shows the near miss but never a URI to act on.
    assert by_title["Creep"]["spotify_artist"] == "Stone Temple Pilots"
    assert by_title["Creep"]["spotify_uri"] == ""
    assert by_title["Idioteque"]["status"] == "not_found"
    assert by_title["Idioteque"]["score"] == ""


def test_dry_run_matches_but_touches_nothing(tmp_path):
    rows = read_rows(write_csv(tmp_path), min_stars=0)
    client = FakeClient(full_catalogue())
    summary = run_import(rows, client, tmp_path / "report.csv", dry_run=True)
    assert summary["matched"] == 3
    assert summary["added"] == 0
    assert client.created == [] and client.added == []
    assert len(read_report(tmp_path / "report.csv")) == 3


def test_no_playlist_is_created_when_nothing_matches(tmp_path):
    rows = read_rows(write_csv(tmp_path), min_stars=0)
    client = FakeClient({})
    summary = run_import(rows, client, tmp_path / "report.csv")
    assert summary["matched"] == 0
    assert client.created == []


def test_report_survives_an_api_failure_partway_through(tmp_path):
    # Matching is the slow part of a run; a failure while adding tracks must
    # not throw away the work already done.
    rows = read_rows(write_csv(tmp_path), min_stars=0)
    client = FakeClient(full_catalogue(), fail_on_add=True)
    with pytest.raises(SpotifyApiError):
        run_import(rows, client, tmp_path / "report.csv")
    assert len(read_report(tmp_path / "report.csv")) == 3


def test_public_flag_reaches_the_api(tmp_path):
    rows = read_rows(write_csv(tmp_path), min_stars=5)
    client = FakeClient(full_catalogue())
    run_import(rows, client, tmp_path / "report.csv", public=True)
    assert client.created[0]["public"] is True


def test_default_playlist_name_carries_the_date():
    from datetime import date

    assert default_playlist_name(date(2026, 8, 2)) == "iTunes Ratings 2026-08-02"
