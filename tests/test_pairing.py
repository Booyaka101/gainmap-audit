from __future__ import annotations

from pathlib import Path

import pytest
from builders import jpeg_bytes
from builders_isobmff import auxc, iinf, infe, ipco, iprp, iref, iref_entry, isobmff_file, pitm

from gainmap_audit import detect, pairing


def _report(state: str, path: str = "x.jpg") -> detect.FileReport:
    return detect.FileReport(Path(path), state, "jpeg", 100)


@pytest.mark.parametrize(
    "source_state,export_state,expected",
    [
        (detect.ULTRAHDR, detect.ULTRAHDR, pairing.OK),
        (detect.ISO_JPEG, detect.APPLE_AUX, pairing.OK),
        (detect.ULTRAHDR, detect.NONE, pairing.STRIPPED),
        (detect.APPLE_AUX, detect.ORPHANED, pairing.ORPHANED),
        (detect.ISO_HEIF, detect.NONE, pairing.STRIPPED),
        (detect.NONE, detect.ORPHANED, pairing.DEGRADED),
        (detect.NONE, detect.ULTRAHDR, pairing.ADDED),
        (detect.NONE, detect.NONE, pairing.SDR_BOTH),
        (detect.ERROR, detect.NONE, pairing.ERROR),
        (detect.NONE, detect.ERROR, pairing.ERROR),
    ],
)
def test_verdict_matrix(source_state, export_state, expected):
    assert pairing.verdict(_report(source_state), _report(export_state)) == expected


def test_worked_example_heic_to_jpg_is_stripped(tmp_path):
    # The brief's worked example: an apple-aux HEIC round-tripped through an
    # editor loses its gain map and comes back as a plain JPEG with none.
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

    source_report = detect.classify(source_dir / "IMG_0421.HEIC")
    export_report = detect.classify(export_dir / "IMG_0421.jpg")
    assert source_report.state == detect.APPLE_AUX
    assert export_report.state == detect.NONE
    assert pairing.verdict(source_report, export_report) == pairing.STRIPPED


def test_stem_key_is_case_insensitive():
    assert pairing.stem_key(Path("IMG_0421.HEIC")) == pairing.stem_key(Path("img_0421.jpg"))


def test_relpath_key_matches_subdirectory_structure(tmp_path):
    key = pairing.relpath_key(tmp_path)
    a = tmp_path / "trip" / "IMG_01.HEIC"
    b = tmp_path / "trip" / "img_01.jpg"
    assert key(a, tmp_path) == key(b, tmp_path)


def test_relpath_key_differs_across_subdirectories(tmp_path):
    key = pairing.relpath_key(tmp_path)
    a = tmp_path / "a" / "x.jpg"
    b = tmp_path / "b" / "x.jpg"
    assert key(a, tmp_path) != key(b, tmp_path)


def test_iter_image_files_skips_sidecars_and_hidden_and_junk_dirs(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(jpeg_bytes([]))
    (tmp_path / "photo.xmp").write_bytes(b"<x:xmpmeta/>")
    (tmp_path / "photo.aae").write_bytes(b"plist")
    (tmp_path / ".hidden.jpg").write_bytes(jpeg_bytes([]))
    junk = tmp_path / "@eaDir"
    junk.mkdir()
    (junk / "thumb.jpg").write_bytes(jpeg_bytes([]))
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.jpg").write_bytes(jpeg_bytes([]))

    found = list(pairing.iter_image_files(tmp_path, False, detect.IMAGE_EXTENSIONS))
    assert [p.name for p in found] == ["photo.jpg"]


def test_iter_image_files_recursive_descends_into_subdirs(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(jpeg_bytes([]))
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.heic").write_bytes(b"\x00\x00\x00\x18ftypheic")

    found = sorted(
        p.name for p in pairing.iter_image_files(tmp_path, True, detect.IMAGE_EXTENSIONS)
    )
    assert found == ["nested.heic", "photo.jpg"]


def test_iter_image_files_ignores_non_image_extensions(tmp_path):
    (tmp_path / "readme.txt").write_text("hi")
    (tmp_path / "photo.jpg").write_bytes(jpeg_bytes([]))
    found = list(pairing.iter_image_files(tmp_path, False, detect.IMAGE_EXTENSIONS))
    assert [p.name for p in found] == ["photo.jpg"]


def test_build_pairs_stem_mode_matches_across_extension_change():
    sources = [Path("a/IMG_0421.HEIC")]
    exports = [Path("b/img_0421.jpg")]
    pairs = pairing.build_pairs(sources, exports, Path("a"), Path("b"), "stem")
    assert len(pairs) == 1
    assert pairs[0].sources == (Path("a/IMG_0421.HEIC"),)
    assert pairs[0].exports == (Path("b/img_0421.jpg"),)


def test_build_pairs_unpaired_source_and_export():
    sources = [Path("a/only-src.jpg")]
    exports = [Path("b/only-exp.jpg")]
    pairs = pairing.build_pairs(sources, exports, Path("a"), Path("b"), "stem")
    by_stem = {p.stem: p for p in pairs}
    assert by_stem["only-src"].unpaired_source
    assert by_stem["only-exp"].unpaired_export


def test_build_pairs_ambiguous_multiple_exports_same_stem():
    sources = [Path("a/x.heic")]
    exports = [Path("b/x.jpg"), Path("b/x.avif")]
    pairs = pairing.build_pairs(sources, exports, Path("a"), Path("b"), "stem")
    assert len(pairs) == 1
    assert pairs[0].ambiguous


def test_build_pairs_ambiguous_multiple_sources_same_stem():
    # A dual-format capture (HEIC + JPG saved under the same name) must not
    # silently collapse to reports[pair.sources[0]] and drop the other file.
    sources = [Path("a/x.heic"), Path("a/x.jpg")]
    exports = [Path("b/x.png")]
    pairs = pairing.build_pairs(sources, exports, Path("a"), Path("b"), "stem")
    assert len(pairs) == 1
    assert pairs[0].ambiguous

    reports = {
        sources[0]: _report(detect.APPLE_AUX, "a/x.heic"),
        sources[1]: _report(detect.NONE, "a/x.jpg"),
        exports[0]: _report(detect.NONE, "b/x.png"),
    }
    diffs = pairing.diff_pair(pairs[0], reports)
    assert len(diffs) == 1
    assert diffs[0].verdict == pairing.AMBIGUOUS
    assert len(diffs[0].extra_sources) == 1
    assert diffs[0].extra_sources[0].path == Path("a/x.jpg")


def test_diff_pair_unpaired_and_ambiguous():
    src = Path("a/x.jpg")
    reports = {src: _report(detect.NONE, "a/x.jpg")}
    pair = pairing.Pair("x", (src,), ())
    diffs = pairing.diff_pair(pair, reports)
    assert len(diffs) == 1
    assert diffs[0].verdict == pairing.UNPAIRED_SOURCE

    exp = Path("b/x.jpg")
    reports2 = {exp: _report(detect.NONE, "b/x.jpg")}
    pair2 = pairing.Pair("x", (), (exp,))
    diffs2 = pairing.diff_pair(pair2, reports2)
    assert diffs2[0].verdict == pairing.UNPAIRED_EXPORT

    exp2 = Path("b/x.avif")
    reports3 = {
        src: _report(detect.ULTRAHDR, "a/x.jpg"),
        exp: _report(detect.NONE, "b/x.jpg"),
        exp2: _report(detect.NONE, "b/x.avif"),
    }
    pair3 = pairing.Pair("x", (src,), (exp, exp2))
    diffs3 = pairing.diff_pair(pair3, reports3)
    assert len(diffs3) == 1
    assert diffs3[0].verdict == pairing.AMBIGUOUS
    assert len(diffs3[0].extra_exports) == 1
