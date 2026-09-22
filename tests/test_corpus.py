"""Classify the committed real-file corpus and check it against tests/corpus/SOURCES.md.

This is the acceptance check from the brief: ``gmaudit scan tests/corpus --json``
must classify every corpus file with non-empty evidence, and each file's state
must match what its upstream project says it actually is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gainmap_audit import detect, jpeg
from gainmap_audit.reader import FileWindow

CORPUS = Path(__file__).parent / "corpus"

EXPECTED_STATE = {
    "small_uhdr.jpg": detect.ISO_JPEG,
    "sample_srgb.jpg": detect.NONE,
    "apple_gainmap_new.jpg": detect.APPLE_AUX,
    "paris_exif_xmp_gainmap_bigendian.jpg": detect.ULTRAHDR,
    "colors_sdr_srgb.avif": detect.NONE,
    "seine_sdr_gainmap_notmapbrand.avif": detect.ISO_HEIF,
    "apple_hdr_sample.heic": detect.APPLE_AUX,
}


def _corpus_files() -> list[Path]:
    return sorted(p for p in CORPUS.iterdir() if p.suffix.lower() in detect.IMAGE_EXTENSIONS)


def test_sources_md_lists_every_corpus_file():
    on_disk = {p.name for p in _corpus_files()}
    assert on_disk == set(EXPECTED_STATE)


@pytest.mark.parametrize("name", sorted(EXPECTED_STATE))
def test_corpus_file_matches_documented_state(name):
    report = detect.classify(CORPUS / name)
    assert report.state == EXPECTED_STATE[name], report.evidence
    assert report.error is None


@pytest.mark.parametrize("name", sorted(EXPECTED_STATE))
def test_corpus_file_has_non_empty_evidence(name):
    report = detect.classify(CORPUS / name)
    assert report.evidence
    assert all(e.detail for e in report.evidence)


def test_small_uhdr_carries_mpf_and_iso_app2():
    report = detect.classify(CORPUS / "small_uhdr.jpg")
    assert report.state == detect.ISO_JPEG
    assert any(e.rule == "mpf" for e in report.evidence)


def _cut_off_the_gain_map(path: Path) -> bytes:
    """The real file's primary image alone, with its XMP and MPF segment intact."""
    with FileWindow(path) as win:
        images = jpeg.walk(win)
        assert len(images) == 2, "fixture needs a real two-image file"
        return win.read_at(0, images[1].offset)


def test_real_ultrahdr_without_its_payload_is_orphaned(tmp_path):
    # An editor that re-encodes the primary and copies the XMP across leaves a file
    # whose metadata still names a gain map that is not there. Reporting that as
    # `ultrahdr` would tell someone their round trip was clean when it was not.
    path = tmp_path / "payload_gone.jpg"
    path.write_bytes(_cut_off_the_gain_map(CORPUS / "paris_exif_xmp_gainmap_bigendian.jpg"))
    report = detect.classify(path)
    assert report.state == detect.ORPHANED
    assert not report.has_gain_map
    assert report.gain_map is None


def test_every_gain_map_state_has_a_locatable_payload():
    # The invariant behind the orphaned rule: a state in GAIN_MAP_STATES means we
    # found the payload, not that the metadata claimed one. JPEG locates it by byte
    # offset, ISOBMFF by item id.
    for path in _corpus_files():
        report = detect.classify(path)
        if not report.has_gain_map:
            continue
        assert report.gain_map is not None, path.name
        gm = report.gain_map
        assert gm.offset is not None or gm.item_id is not None, path.name
        if gm.offset is not None:
            assert gm.offset < report.size, path.name
