from __future__ import annotations

import json
from pathlib import Path

import pytest
from builders import jpeg_bytes
from builders_isobmff import auxc, iinf, infe, ipco, iprp, iref, iref_entry, isobmff_file, pitm

from gainmap_audit import cli, detect

CORPUS = Path(__file__).parent / "corpus"


def test_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "gmaudit" in out


def test_no_command_prints_help_and_exits_usage(capsys):
    code = cli.main([])
    assert code == cli.EXIT_USAGE
    assert "usage" in capsys.readouterr().err.lower()


def test_bad_fail_on_value_exits_usage():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["check", "x.jpg", "--fail-on", "not-a-real-state"])
    assert excinfo.value.code == cli.EXIT_USAGE


def test_scan_nonexistent_directory_is_usage_error(capsys):
    code = cli.main(["scan", "/no/such/directory/at/all"])
    assert code == cli.EXIT_USAGE
    assert "error" in capsys.readouterr().err.lower()


def test_check_missing_file_is_a_finding_not_a_crash(capsys):
    code = cli.main(["check", "/no/such/file.jpg"])
    assert code == cli.EXIT_FINDINGS
    out = capsys.readouterr().out
    assert "ERROR" in out


def test_check_clean_file_exits_ok(capsys):
    code = cli.main(["check", str(CORPUS / "sample_srgb.jpg")])
    assert code == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "NONE" in out


def test_check_json_schema(capsys):
    code = cli.main(["check", str(CORPUS / "small_uhdr.jpg"), "--json"])
    assert code == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert set(data.keys()) == {"files"}
    entry = data["files"][0]
    assert entry["state"] == "iso-jpeg"
    assert entry["rules_fired"] == ["iso-jpeg"]
    assert isinstance(entry["evidence"], list) and entry["evidence"]
    assert entry["error"] is None


def test_scan_corpus_classifies_every_file(capsys):
    code = cli.main(["scan", str(CORPUS), "--json"])
    assert code == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    names = {Path(f["path"]).name for f in data["files"]}
    assert names == {
        "small_uhdr.jpg",
        "sample_srgb.jpg",
        "apple_gainmap_new.jpg",
        "paris_exif_xmp_gainmap_bigendian.jpg",
        "colors_sdr_srgb.avif",
        "seine_sdr_gainmap_notmapbrand.avif",
        "apple_hdr_sample.heic",
    }
    assert all(f["evidence"] for f in data["files"])


def test_scan_fail_on_custom_state_triggers_exit_1(capsys):
    code = cli.main(["scan", str(CORPUS), "--fail-on", "iso-jpeg", "--json"])
    capsys.readouterr()
    assert code == cli.EXIT_FINDINGS


def test_check_csv_writes_file(tmp_path, capsys):
    csv_path = tmp_path / "out.csv"
    code = cli.main(["check", str(CORPUS / "sample_srgb.jpg"), "--csv", str(csv_path)])
    capsys.readouterr()
    assert code == cli.EXIT_OK
    text = csv_path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "path,state,container,size,rules_fired,evidence,error"
    assert "sample_srgb.jpg" in text


def test_diff_worked_example_heic_to_jpg_stripped(tmp_path, capsys):
    source_dir = tmp_path / "src"
    export_dir = tmp_path / "export"
    source_dir.mkdir()
    export_dir.mkdir()

    props = ipco([auxc(detect.isobmff.APPLE_GAIN_MAP_AUX)])
    heic = isobmff_file(
        "heic",
        ["heic", "mif1"],
        [
            pitm(1),
            iinf([infe(1, "hvc1", b"Primary"), infe(2, "hvc1", b"Aux")]),
            iref([iref_entry("auxl", 2, [1])]),
            iprp([props]),
        ],
    )
    (source_dir / "IMG_0421.HEIC").write_bytes(heic)
    (export_dir / "IMG_0421.jpg").write_bytes(jpeg_bytes([]))

    code = cli.main(["diff", str(source_dir), str(export_dir)])
    out = capsys.readouterr().out
    assert code == cli.EXIT_FINDINGS
    assert "STRIPPED" in out
    assert "IMG_0421.HEIC -> IMG_0421.jpg" in out
    assert "iso-heif -> none" not in out  # this fixture is apple-aux, not iso-heif
    assert "apple-aux -> none" in out


def test_diff_json_schema(tmp_path, capsys):
    source_dir = tmp_path / "src"
    export_dir = tmp_path / "export"
    source_dir.mkdir()
    export_dir.mkdir()
    (source_dir / "a.jpg").write_bytes(jpeg_bytes([]))
    (export_dir / "a.jpg").write_bytes(jpeg_bytes([]))

    code = cli.main(["diff", str(source_dir), str(export_dir), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK  # sdr-both is not in the default fail-on set
    assert set(data.keys()) == {"pairs"}
    pair = data["pairs"][0]
    assert pair["verdict"] == "sdr-both"
    assert pair["source"]["state"] == "none"
    assert pair["export"]["state"] == "none"


def test_diff_unpaired_source_and_export(tmp_path, capsys):
    source_dir = tmp_path / "src"
    export_dir = tmp_path / "export"
    source_dir.mkdir()
    export_dir.mkdir()
    (source_dir / "only-source.jpg").write_bytes(jpeg_bytes([]))
    (export_dir / "only-export.jpg").write_bytes(jpeg_bytes([]))

    code = cli.main(["diff", str(source_dir), str(export_dir), "--json"])
    data = json.loads(capsys.readouterr().out)
    verdicts = {p["stem"]: p["verdict"] for p in data["pairs"]}
    assert verdicts == {"only-source": "unpaired-source", "only-export": "unpaired-export"}
    assert code == cli.EXIT_OK  # neither verdict is in the default fail-on set
