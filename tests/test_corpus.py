"""Classify the committed real-file corpus and check it against tests/corpus/SOURCES.md.

This is the acceptance check from the brief: ``gmaudit scan tests/corpus --json``
must classify every corpus file with non-empty evidence, and each file's state
must match what its upstream project says it actually is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gainmap_audit import detect

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
