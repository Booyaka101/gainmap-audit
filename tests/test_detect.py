from __future__ import annotations

from builders import (
    apple_aux_xmp,
    hdrgm_xmp,
    iso_app2,
    jpeg_bytes,
    mpf_app2,
    xmp_app1,
)
from builders_isobmff import (
    auxc,
    box,
    ftyp,
    iinf,
    infe,
    ipco,
    ipma,
    iprp,
    iref,
    iref_entry,
    isobmff_file,
    pitm,
)

from gainmap_audit import detect


def _write(tmp_path, data: bytes, name: str):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _concat_mpf(primary_app1: bytes, secondary_body: bytes) -> bytes:
    """A real primary+secondary JPEG pair with a correct MPF data_offset.

    Mirrors the two-pass probe used in test_jpeg.py: the MPF entry offset is
    relative to the MP Header, which only exists once the MPF segment itself
    is laid out, so we measure with a placeholder before writing the real one.
    """
    probe = jpeg_bytes([primary_app1, mpf_app2([(0x030000, 0, 0), (0, len(secondary_body), 0)])])
    base = 2 + len(primary_app1) + 4 + len(b"MPF\x00")
    data_offset = len(probe) - base
    data = jpeg_bytes(
        [primary_app1, mpf_app2([(0x030000, len(probe), 0), (0, len(secondary_body), data_offset)])]
    )
    assert len(data) == len(probe)
    return data + secondary_body


def test_none_state_for_plain_jpeg(tmp_path):
    path = _write(tmp_path, jpeg_bytes([]), "plain.jpg")
    report = detect.classify(path)
    assert report.state == detect.NONE
    assert not report.has_gain_map
    assert report.evidence


def test_ultrahdr_rule_fires_on_primary_hdrgm_version(tmp_path):
    data = jpeg_bytes([xmp_app1(hdrgm_xmp(with_container=True))])
    path = _write(tmp_path, data, "uhdr.jpg")
    report = detect.classify(path)
    assert report.state == detect.ULTRAHDR
    assert detect.ULTRAHDR in report.rules_fired
    assert report.gain_map is not None
    assert report.gain_map.source == "gcontainer"


def test_iso_jpeg_rule_fires_on_app2_namespace_literal(tmp_path):
    data = jpeg_bytes([iso_app2()])
    path = _write(tmp_path, data, "iso.jpg")
    report = detect.classify(path)
    assert report.state == detect.ISO_JPEG
    assert report.gain_map.source == "iso-app2"


def test_iso_jpeg_prefix_fallback(tmp_path):
    data = jpeg_bytes([iso_app2(namespace=b"urn:iso:std:iso:ts:21496\x00", extra=b"")])
    path = _write(tmp_path, data, "iso-prefix.jpg")
    report = detect.classify(path)
    assert report.state == detect.ISO_JPEG
    assert "pre-final prefix" in report.evidence[-1].detail or any(
        "pre-final prefix" in e.detail for e in report.evidence
    )


def test_ultrahdr_outranks_iso_jpeg_when_both_fire(tmp_path):
    data = jpeg_bytes([xmp_app1(hdrgm_xmp()), iso_app2()])
    path = _write(tmp_path, data, "both.jpg")
    report = detect.classify(path)
    assert report.state == detect.ULTRAHDR
    assert set(report.rules_fired) == {detect.ULTRAHDR, detect.ISO_JPEG}


def test_apple_aux_jpeg_rule_fires_on_mpf_secondary_xmp(tmp_path):
    # Primary XMP with no hdrgm:Version, so the ULTRAHDR rule cannot fire.
    primary = xmp_app1(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description/>'
        "</rdf:RDF></x:xmpmeta>"
    )
    data = _concat_mpf(primary, jpeg_bytes([xmp_app1(apple_aux_xmp())]))
    path = _write(tmp_path, data, "apple.jpg")
    report = detect.classify(path)
    assert report.state == detect.APPLE_AUX
    assert report.gain_map.source == "mpf-apple"


def test_orphaned_when_mpf_secondary_stranded(tmp_path):
    primary = xmp_app1(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description/>'
        "</rdf:RDF></x:xmpmeta>"
    )
    secondary = jpeg_bytes([xmp_app1(hdrgm_xmp())])
    data = _concat_mpf(primary, secondary)
    path = _write(tmp_path, data, "orphaned.jpg")
    report = detect.classify(path)
    assert report.state == detect.ORPHANED
    assert not report.has_gain_map
    assert "hdrgm XMP packet" in report.evidence[-1].detail


def test_orphaned_when_mpf_secondary_has_no_xmp_at_all(tmp_path):
    primary = xmp_app1(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description/>'
        "</rdf:RDF></x:xmpmeta>"
    )
    secondary = jpeg_bytes([])
    data = _concat_mpf(primary, secondary)
    path = _write(tmp_path, data, "orphaned2.jpg")
    report = detect.classify(path)
    assert report.state == detect.ORPHANED
    assert "not a thumbnail" in report.evidence[-1].detail


def test_thumbnail_mp_type_is_not_orphaned(tmp_path):
    primary = xmp_app1(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description/>'
        "</rdf:RDF></x:xmpmeta>"
    )
    secondary = jpeg_bytes([])
    thumb_type = 0x010001  # MPF "large thumbnail" attribute, excluded by MP_TYPE_NON_GAINMAP
    probe = jpeg_bytes(
        [primary, mpf_app2([(0x030000, 0, 0), (thumb_type, len(secondary), 0)])]
    )
    base = 2 + len(primary) + 4 + len(b"MPF\x00")
    data_offset = len(probe) - base
    data = jpeg_bytes(
        [primary, mpf_app2([(0x030000, len(probe), 0), (thumb_type, len(secondary), data_offset)])]
    )
    path = _write(tmp_path, data + secondary, "thumb.jpg")
    report = detect.classify(path)
    assert report.state == detect.NONE


def test_empty_file_is_error(tmp_path):
    path = _write(tmp_path, b"", "empty.jpg")
    report = detect.classify(path)
    assert report.state == detect.ERROR
    assert report.error == "empty file"


def test_truncated_jpeg_is_error(tmp_path):
    data = jpeg_bytes([xmp_app1(hdrgm_xmp())])[:20]
    path = _write(tmp_path, data, "truncated.jpg")
    report = detect.classify(path)
    assert report.state == detect.ERROR
    assert report.error


def test_missing_file_is_error(tmp_path):
    report = detect.classify(tmp_path / "does-not-exist.jpg")
    assert report.state == detect.ERROR


def test_not_an_image_is_error(tmp_path):
    path = _write(tmp_path, b"this is a text file, not a photo\n" * 4, "notes.jpg")
    report = detect.classify(path)
    assert report.state == detect.ERROR
    assert "not a JPEG" in report.error


def test_iso_heif_rule_fires_on_tmap_item(tmp_path):
    meta_children = [
        pitm(1),
        iinf([infe(1, "av01", b"Color"), infe(2, "tmap", b"GMap"), infe(3, "av01", b"GMap")]),
        iref([iref_entry("dimg", 2, [1, 3])]),
    ]
    data = isobmff_file("avif", ["avif", "mif1", "miaf"], meta_children)
    path = _write(tmp_path, data, "tmap.avif")
    report = detect.classify(path)
    assert report.state == detect.ISO_HEIF
    assert report.gain_map.item_id == 3


def test_apple_aux_heif_rule_fires_on_auxc(tmp_path):
    props = ipco([auxc(detect.isobmff.APPLE_GAIN_MAP_AUX)])
    meta_children = [
        pitm(1),
        iinf([infe(1, "hvc1", b"Primary"), infe(2, "hvc1", b"Aux")]),
        iref([iref_entry("auxl", 2, [1])]),
        iprp([props]),
    ]
    data = isobmff_file("heic", ["heic", "mif1"], meta_children)
    path = _write(tmp_path, data, "auxc.heic")
    report = detect.classify(path)
    assert report.state == detect.APPLE_AUX
    assert report.gain_map.item_id == 2


def test_apple_aux_heif_rule_picks_gain_map_item_not_depth_item(tmp_path):
    # A file with two auxiliary images (depth map listed first, gain map
    # second) must report the gain map's item_id, resolved through the real
    # ipco/ipma association, not the first item that merely has an auxl ref.
    depth_type = "urn:mpeg:mpegB:cicp:aux:disparity"
    props = ipco([auxc(depth_type), auxc(detect.isobmff.APPLE_GAIN_MAP_AUX)])
    meta_children = [
        pitm(1),
        iinf(
            [
                infe(1, "hvc1", b"Primary"),
                infe(2, "hvc1", b"Depth"),
                infe(3, "hvc1", b"GainMap"),
            ]
        ),
        iref([iref_entry("auxl", 2, [1]), iref_entry("auxl", 3, [1])]),
        iprp([props, ipma([(2, [1]), (3, [2])])]),
    ]
    data = isobmff_file("heic", ["heic", "mif1"], meta_children)
    path = _write(tmp_path, data, "depth_and_gainmap.heic")
    report = detect.classify(path)
    assert report.state == detect.APPLE_AUX
    assert report.gain_map.item_id == 3


def test_apple_aux_urn_scan_fallback_when_no_meta_box(tmp_path):
    urn = b"urn:com:apple:photo:2020:aux:hdrgainmap"
    data = ftyp("heic", ["heic", "mif1"]) + box("free", b"\x00" * 4 + urn + b"\x00" * 4)
    path = _write(tmp_path, data, "scan.heic")
    report = detect.classify(path)
    assert report.state == detect.APPLE_AUX
    assert report.gain_map.source == "urn-scan"
    assert any(e.rule == "urn-scan" for e in report.evidence)


def test_isobmff_with_no_gain_map_signal_is_none(tmp_path):
    meta_children = [pitm(1), iinf([infe(1, "hvc1", b"Photo")])]
    data = isobmff_file("heic", ["heic", "mif1"], meta_children)
    path = _write(tmp_path, data, "sdr.heic")
    report = detect.classify(path)
    assert report.state == detect.NONE


def test_truncated_isobmff_is_error_or_none(tmp_path):
    data = isobmff_file("heic", ["heic", "mif1"], [pitm(1)])[:16]
    path = _write(tmp_path, data, "trunc.heic")
    report = detect.classify(path)
    # A stream this short has no meta box to walk at all: the classifier must
    # not crash, whether it reports NONE (no signal found) or ERROR.
    assert report.state in (detect.NONE, detect.ERROR)
