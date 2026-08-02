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
    assert "5 tracks" in summary
    assert "2 manually rated" in summary
    assert "2 with plays" in summary
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


def test_main_unwritable_output_dir(tmp_path, capsys):
    # A file where a directory is expected makes mkdir(parents=True) raise
    # OSError portably (no need for platform-specific permission tricks).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    bad_out = blocker / "export"
    code = main(["--library", str(FIXTURE), "--out", str(bad_out)])
    assert code == 3
    err = capsys.readouterr().err
    assert str(bad_out) in err


def test_dunder_main_does_not_exit_on_import():
    # __main__.py must guard sys.exit(main()) behind `if __name__ ==
    # "__main__"` -- otherwise merely importing the module (as this test
    # does) terminates the interpreter.
    import itunes_ratings_exporter.__main__  # noqa: F401
