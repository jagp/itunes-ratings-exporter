from pathlib import Path

from itunes_ratings_exporter.parser import parse_library, location_to_path

FIXTURE = Path(__file__).parent / "fixtures" / "library.xml"


def _track(lib, pid):
    return next(t for t in lib["tracks"] if t["persistent_id"] == pid)


def test_manually_rated_track_fields():
    lib = parse_library(FIXTURE)
    t = _track(lib, "AAAA1111AAAA1111")
    assert t["title"] == "Túnel"
    assert t["artist"] == "Aria"
    assert t["album_artist"] == "Aria"
    assert t["album"] == "Cañón"
    assert t["rating_stars"] == 5
    assert t["rating_computed"] is False
    assert t["play_count"] == 12
    assert t["last_played"] == "2025-06-01T10:00:00Z"
    assert t["duration_ms"] == 215000
    assert t["year"] == 2020
    assert t["track_number"] == 3
    assert t["disc_number"] == 1
    assert t["genre"] == "Rock"
    assert t["compilation"] is False
    assert t["purchased"] is False
    assert t["kind"] == "MPEG audio file"
    assert t["extra_ids"] == {"Track ID": 101}


def test_computed_rating_flagged():
    t = _track(parse_library(FIXTURE), "BBBB2222BBBB2222")
    assert t["rating_stars"] == 3
    assert t["rating_computed"] is True


def test_unrated_cloud_track_has_blanks():
    t = _track(parse_library(FIXTURE), "CCCC3333CCCC3333")
    assert t["rating_stars"] is None
    assert t["play_count"] is None
    assert t["last_played"] == ""
    assert t["file_path"] == ""


def test_purchased_compilation_flags():
    t = _track(parse_library(FIXTURE), "DDDD4444DDDD4444")
    assert t["purchased"] is True
    assert t["compilation"] is True
    assert t["rating_stars"] == 4


def test_location_to_path_mapped_network_drive():
    assert (
        location_to_path("file://localhost/Z:/Music/Aria/T%C3%BAnel.mp3")
        == "Z:\Music\Aria\Túnel.mp3"
    )


def test_location_to_path_unc_host_in_netloc():
    assert (
        location_to_path("file://SYNOLOGY/music/Aria/song.mp3")
        == "\\SYNOLOGY\music\Aria\song.mp3"
    )


def test_location_to_path_unc_leading_slashes():
    assert (
        location_to_path("file://///SYNOLOGY/music/Aria/song.mp3")
        == "\\SYNOLOGY\music\Aria\song.mp3"
    )


def test_location_to_path_no_host_drive_letter():
    assert location_to_path("file:///C:/Music/song.mp3") == "C:\Music\song.mp3"
