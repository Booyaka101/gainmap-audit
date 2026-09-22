"""Cross-verification against libultrahdr's ultrahdr_app.

The plumbing is exercised with a stub binary that mimics the real contract,
which is what was established by running the real thing (v2.0.2):

    -P on a gain mapped file  -> exit 0, "Ultra HDR Image: Yes" plus the metadata
    -P on an SDR file         -> exit 127, "input uhdr image does not contain
                                 gainmap image" on stderr
    an option it lacks        -> exit 127, "unsupported option -X" plus the usage

Probe mode writes nothing, unlike the -m 1 decode that dumps outrgb.raw into the
working directory. ``test_real_ultrahdr_app_agrees_with_us`` runs the actual
binary when one is available, so the stub's contract can't quietly drift.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from builders import concat_mpf, hdrgm_xmp, jpeg_bytes, xmp_app1

from gainmap_audit import cli

CORPUS = Path(__file__).parent / "corpus"

# Set GMAUDIT_ULTRAHDR to a built ultrahdr_app to run the real-binary test.
_REAL = os.environ.get("GMAUDIT_ULTRAHDR") or shutil.which("ultrahdr_app")


# What the real binary prints, not what cli.py looks for, so a change to
# either side has to be reconciled against the measured contract above.
_NO_GAINMAP_MESSAGE = "input uhdr image does not contain gainmap image"
_NO_PROBE_MESSAGE = "unsupported option -P"


def _probe_guard(probe: str, windows: bool) -> list[str]:
    """Lines that make the stub fail either with, or without, the -P probe flag."""
    if probe == "ignore":
        return []
    on_probe = probe == "unsupported"
    message = _NO_PROBE_MESSAGE if on_probe else _NO_GAINMAP_MESSAGE
    if windows:
        # findstr sets errorlevel 1 when it found nothing.
        test = "if not errorlevel 1" if on_probe else "if errorlevel 1"
        return [
            'echo %* | findstr /C:" -P" >nul',
            f"{test} (echo {message} 1>&2 & exit /b 127)",
        ]
    compare = "=" if on_probe else "!="
    return [
        'case " $*" in *" -P"*) seen=yes ;; *) seen=no ;; esac',
        f'if [ "$seen" {compare} yes ]; then echo "{message}" >&2; exit 127; fi',
    ]


def stub(tmp_path: Path, exit_code: int, message: str = "", probe: str = "ignore") -> str:
    """An executable that writes a raw dump to cwd, then reports as told.

    ``probe`` is how it treats -P: "ignore" behaves the same either way,
    "required" reports no gain map without it, and "unsupported" rejects it the
    way libultrahdr before 1.5.0 does.
    """
    if os.name == "nt":
        path = tmp_path / "ultrahdr_app.cmd"
        lines = ["@echo off", *_probe_guard(probe, True), "echo dump> outrgb.raw"]
        if message:
            lines.append(f"echo {message} 1>&2")
        lines.append(f"exit /b {exit_code}")
        path.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
    else:
        path = tmp_path / "ultrahdr_app"
        lines = ["#!/bin/sh", *_probe_guard(probe, False), "echo dump > outrgb.raw"]
        if message:
            lines.append(f'echo "{message}" >&2')
        lines.append(f"exit {exit_code}")
        path.write_text("\n".join(lines) + "\n", encoding="ascii")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def verdicts(captured_out: str) -> list[str | None]:
    """The ``ultrahdr`` field per file. ``"ultrahdr"`` is also a state name, so
    these are read out of the parsed JSON rather than matched as a substring."""
    return [f.get("ultrahdr") for f in json.loads(captured_out)["files"]]


def gain_map_jpeg(tmp_path: Path) -> Path:
    """A real two-image pair, so gmaudit's own verdict is ``ultrahdr`` and a
    stub reporting "decoded" counts as agreement rather than a disagreement."""
    path = tmp_path / "has_map.jpg"
    path.write_bytes(concat_mpf([xmp_app1(hdrgm_xmp())], jpeg_bytes([])))
    return path


def sdr_jpeg(tmp_path: Path) -> Path:
    path = tmp_path / "plain.jpg"
    path.write_bytes(jpeg_bytes([]))
    return path


def test_stub_contract_matches_what_the_real_binary_does(tmp_path):
    # Guards the assumption the other stub tests rest on: a .cmd/.sh stub really
    # does propagate its exit code and stderr through subprocess on this platform.
    binary = stub(tmp_path, 127, "input uhdr image does not contain gainmap image")
    proc = subprocess.run([binary], capture_output=True, text=True, cwd=tmp_path)
    assert proc.returncode == 127
    assert "does not contain gainmap image" in proc.stderr


def test_decoded_agrees_and_prints_no_warning(tmp_path, capsys):
    path = gain_map_jpeg(tmp_path)
    code = cli.main(["check", "--verify-with-ultrahdr", stub(tmp_path, 0), str(path)])
    captured = capsys.readouterr()
    assert code == cli.EXIT_OK
    assert "disagreement" not in captured.err


def test_no_gainmap_message_is_recognised(tmp_path, capsys):
    path = sdr_jpeg(tmp_path)
    binary = stub(tmp_path, 127, "input uhdr image does not contain gainmap image")
    cli.main(["check", "--verify-with-ultrahdr", binary, str(path), "--json"])
    captured = capsys.readouterr()
    assert verdicts(captured.out) == ["no-gainmap"]
    assert "disagreement" not in captured.err


def test_disagreement_is_reported_when_the_tool_decodes_an_sdr_file(tmp_path, capsys):
    # gmaudit sees no gain map, the tool decoded one. That is the case the flag
    # exists to surface, and nothing in the real corpus triggers it.
    path = sdr_jpeg(tmp_path)
    cli.main(["check", "--verify-with-ultrahdr", stub(tmp_path, 0), str(path)])
    captured = capsys.readouterr()
    assert "!! disagreement" in captured.err
    assert "gmaudit says none" in captured.err
    assert "says decoded" in captured.err


def test_other_failure_is_undecodable_not_a_disagreement(tmp_path, capsys):
    # The real binary rejects a file whose XMP lacks hdrgm:GainMapMax. It is not
    # claiming the gain map is absent, so it must not count as disagreeing.
    path = gain_map_jpeg(tmp_path)
    binary = stub(tmp_path, 127, "xml parse error, could not find attribute hdrgm:GainMapMax")
    cli.main(["check", "--verify-with-ultrahdr", binary, str(path), "--json"])
    captured = capsys.readouterr()
    assert verdicts(captured.out) == [
        "undecodable: xml parse error, could not find attribute hdrgm:GainMapMax"
    ]
    assert "disagreement" not in captured.err


def test_relative_path_still_resolves_from_the_scratch_directory(tmp_path, monkeypatch, capsys):
    # The subprocess runs with cwd set elsewhere, so a relative argument has to
    # be made absolute before being handed over.
    photos = tmp_path / "photos"
    photos.mkdir()
    gain_map_jpeg(photos)
    monkeypatch.chdir(photos)
    cli.main(["check", "--verify-with-ultrahdr", stub(tmp_path, 0), "has_map.jpg", "--json"])
    captured = capsys.readouterr()
    assert verdicts(captured.out) == ["decoded"]


def test_missing_binary_warns_and_leaves_the_field_unset(tmp_path, capsys):
    path = gain_map_jpeg(tmp_path)
    code = cli.main(
        ["check", "--verify-with-ultrahdr", str(tmp_path / "nope"), str(path), "--json"]
    )
    captured = capsys.readouterr()
    assert code == cli.EXIT_OK
    assert "not found, skipping verification" in captured.err
    assert verdicts(captured.out) == [None]


def test_non_jpeg_is_not_handed_to_the_tool(tmp_path, capsys):
    # ultrahdr_app only reads JPEG, so AVIF/HEIC must be left alone.
    cli.main(
        [
            "check",
            "--verify-with-ultrahdr",
            stub(tmp_path, 0),
            str(CORPUS / "colors_sdr_srgb.avif"),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out)["files"][0]["container"] != "jpeg"
    assert verdicts(captured.out) == [None]


def test_without_the_flag_the_tool_is_never_invoked(tmp_path, capsys):
    path = gain_map_jpeg(tmp_path)
    cli.main(["check", str(path), "--json"])
    captured = capsys.readouterr()
    assert verdicts(captured.out) == [None]


@pytest.mark.skipif(_REAL is None, reason="no ultrahdr_app available")
def test_real_ultrahdr_app_agrees_with_us(capsys):
    # Both corpus JPEGs the real binary can speak to definitively.
    expected = {"small_uhdr.jpg": "decoded", "sample_srgb.jpg": "no-gainmap"}
    files = [str(CORPUS / name) for name in expected]
    cli.main(["check", "--verify-with-ultrahdr", _REAL, *files, "--json"])
    captured = capsys.readouterr()
    assert verdicts(captured.out) == list(expected.values())
    assert "disagreement" not in captured.err


@pytest.mark.parametrize("probe", ["required", "unsupported"])
def test_probe_is_tried_first_and_the_decode_is_the_fallback(tmp_path, capsys, probe):
    # "required" only succeeds when it was handed -P, so a decoded verdict proves
    # the probe was tried. "unsupported" rejects -P the way libultrahdr before
    # 1.5.0 does, so a decoded verdict proves the fallback ran rather than the
    # complaint being read as a verdict.
    path = gain_map_jpeg(tmp_path)
    binary = stub(tmp_path, 0, probe=probe)
    cli.main(["check", "--verify-with-ultrahdr", binary, str(path), "--json"])
    captured = capsys.readouterr()
    assert verdicts(captured.out) == ["decoded"]
    assert "disagreement" not in captured.err


@pytest.mark.parametrize("probe", ["ignore", "unsupported"])
def test_no_raw_dump_lands_in_the_users_photo_directory(tmp_path, monkeypatch, capsys, probe):
    # The decode writes outrgb.raw to its working directory. Run from a photo folder
    # it would drop a raw frame next to every JPEG it inspected, and the fallback a
    # build without -P takes has to stay in the scratch directory too.
    photos = tmp_path / "photos"
    photos.mkdir()
    path = gain_map_jpeg(photos)
    monkeypatch.chdir(photos)
    cli.main(["check", "--verify-with-ultrahdr", stub(tmp_path, 0, probe=probe), str(path)])
    capsys.readouterr()
    assert sorted(p.name for p in photos.iterdir()) == [path.name]
