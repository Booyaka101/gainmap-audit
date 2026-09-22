"""Classify the committed real-file corpus and check it against tests/corpus/SOURCES.md.

This is the acceptance check from the brief: ``gmaudit scan tests/corpus --json``
must classify every corpus file with non-empty evidence, and each file's state
must match what its upstream project says it actually is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gainmap_audit import detect, isobmff, jpeg
from gainmap_audit.reader import FileWindow

CORPUS = Path(__file__).parent / "corpus"

EXPECTED_STATE = {
    "small_uhdr.jpg": detect.ISO_JPEG,
    "sample_srgb.jpg": detect.NONE,
    "apple_gainmap_new.jpg": detect.APPLE_AUX,
    "paris_exif_xmp_gainmap_bigendian.jpg": detect.ULTRAHDR,
    "paris_xmp_trailing_null.jpg": detect.NONE,
    "colors_sdr_srgb.avif": detect.NONE,
    "seine_sdr_gainmap_notmapbrand.avif": detect.ISO_HEIF,
    "color_grid_gainmap_different_grid.avif": detect.ISO_HEIF,
    "circle_auxl_two_targets.avif": detect.NONE,
    "circle_custom_properties.avif": detect.NONE,
    "colors-animated-8bpc-depth-exif-xmp.avif": detect.NONE,
    "apple_hdr_sample.heic": detect.APPLE_AUX,
}

# Real auxiliary images that are not gain maps. Apple's rule keys on an auxC
# aux_type, so a file carrying some other one must not trip it.
NON_GAIN_MAP_AUX = {
    "circle_auxl_two_targets.avif": "urn:mpeg:mpegB:cicp:systems:auxiliary:alpha",
    "circle_custom_properties.avif": "urn:mpeg:mpegB:cicp:systems:auxiliary:alpha",
    "colors-animated-8bpc-depth-exif-xmp.avif": "urn:mpeg:mpegB:cicp:systems:auxiliary:depth",
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


@pytest.mark.parametrize("name,aux_type", sorted(NON_GAIN_MAP_AUX.items()))
def test_a_real_auxiliary_image_is_not_mistaken_for_a_gain_map(name, aux_type):
    with FileWindow(CORPUS / name) as win:
        meta = isobmff.read_meta(win)
    # Resolving the aux type through ipco/ipma is the point, not just finding the
    # URN somewhere: circle_custom_properties.avif puts unknown boxes in ipco,
    # which shifts the 1-based property_index every ipma entry is written against.
    assert meta.items_with_aux_type(aux_type), meta.aux_types
    assert not meta.items_with_aux_type(isobmff.APPLE_GAIN_MAP_AUX)
    assert detect.classify(CORPUS / name).state == detect.NONE


def test_tmap_gain_map_item_id_survives_a_grid_layout():
    report = detect.classify(CORPUS / "color_grid_gainmap_different_grid.avif")
    assert report.state == detect.ISO_HEIF
    # Base and gain map are both grids built from their own tiles, so the ids run
    # well past the 1/3/4 that a plain two-image file produces.
    assert report.gain_map.item_id == 28


def test_a_real_xmp_packet_with_a_trailing_null_still_reads():
    # Reporting `none` has to mean we read the XMP and found no gain map in it,
    # not that the packet failed to parse and we never looked.
    path = CORPUS / "paris_xmp_trailing_null.jpg"
    with FileWindow(path) as win:
        packets = jpeg.collect_xmp(jpeg.walk(win)[0])
    assert len(packets) == 1
    assert "\x00" not in packets[0].text
    assert packets[0].text.endswith("?>")


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
