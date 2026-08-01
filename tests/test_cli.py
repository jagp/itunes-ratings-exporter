from pathlib import Path

from itunes_ratings_exporter.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "library.xml"


def test_main_exports_all_four_files(tmp_path, capsys):
    out = tmp_path / "export"
    code = main(["--library", str(FIXTURE), "--out", str(out)])
    assert code == 0
    for name in ("tracks.csv", "rated.csv", "playlists.csv", "library.json"):
        assert (out / name).is_file(), name
    summary = capsys.readouterr().out
    assert "4 tracks" in summary
    assert "2 manually rated" in summary
    assert "2 playlists" in summary


def test_main_missing_library_explains_setting(tmp_path, capsys):
    code = main(["--library", str(tmp_path / "nope.xml"), "--out", str(tmp_path)])
    assert code == 2
    err = capsys.readouterr().err
    assert "Share iTunes Library XML" in err


def test_main_malformed_library(tmp_path, capsys):
    bad = tmp_path / "bad.xml"
    bad.write_text("not a plist", encoding="utf-8")
    code = main(["--library", str(bad), "--out", str(tmp_path)])
    assert code == 1
    assert "Could not parse" in capsys.readouterr().err
