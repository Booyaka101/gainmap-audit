from __future__ import annotations

import pytest
from builders import (
    apple_aux_xmp,
    extended_xmp_app1,
    hdrgm_xmp,
    iso_app2,
    jpeg_bytes,
    mpf_app2,
    xmp_app1,
)

from gainmap_audit import jpeg
from gainmap_audit.reader import FileWindow


def _window(tmp_path, data: bytes, name: str = "x.jpg") -> FileWindow:
    path = tmp_path / name
    path.write_bytes(data)
    return FileWindow(path)


def test_walk_single_image_no_mpf(tmp_path):
    data = jpeg_bytes([xmp_app1(hdrgm_xmp())])
    with _window(tmp_path, data) as win:
        images = jpeg.walk(win)
    assert len(images) == 1
    assert images[0].offset == 0


def test_mpf_index_parses_real_ifd_big_endian(tmp_path):
    data = jpeg_bytes([mpf_app2([(0x030000, 500, 0), (0x000000, 300, 700)])])
    with _window(tmp_path, data) as win:
        images = jpeg.walk(win)
        index = jpeg.find_mpf(images[0])
    assert index is not None
    assert index.count == 2
    assert index.big_endian is True
    assert [i.mp_type for i in index.images] == [0x030000, 0]
    assert index.images[1].size == 300
    assert index.images[1].could_be_gain_map is True
    assert index.images[0].could_be_gain_map is False  # primary itself


def test_mpf_index_little_endian(tmp_path):
    data = jpeg_bytes([mpf_app2([(0x030000, 10, 0), (0, 20, 30)], big_endian=False)])
    with _window(tmp_path, data) as win:
        index = jpeg.find_mpf(jpeg.walk(win)[0])
    assert index is not None and index.big_endian is False
    assert index.images[1].size == 20


def test_walk_follows_mpf_offsets_to_concatenated_images(tmp_path):
    # The MPF data_offset is relative to the MP Header (the TIFF header right
    # after "MPF\0"), not to the file start, so it depends on where the MPF
    # segment itself lands in the primary image. Probe with a placeholder
    # offset first to learn that position, then build the real segment: the
    # entry is a fixed-width uint32, so the probe's length already matches.
    xmp_seg = xmp_app1(hdrgm_xmp())
    secondary = jpeg_bytes([xmp_app1(apple_aux_xmp())])
    probe = jpeg_bytes([xmp_seg, mpf_app2([(0x030000, 0, 0), (0, len(secondary), 0)])])
    base = 2 + len(xmp_seg) + 4 + len(b"MPF\x00")
    data_offset = len(probe) - base
    data = jpeg_bytes(
        [xmp_seg, mpf_app2([(0x030000, len(probe), 0), (0, len(secondary), data_offset)])]
    )
    assert len(data) == len(probe)
    full = data + secondary
    path = tmp_path / "concat.jpg"
    path.write_bytes(full)
    with FileWindow(path) as win:
        images = jpeg.walk(win)
    assert len(images) == 2
    assert images[1].offset == len(data)
    xs = jpeg.collect_xmp(images[1])
    assert any("apdi:AuxiliaryImageType" in x.text for x in xs)


def test_walk_falls_back_to_soi_scan_without_mpf(tmp_path):
    primary = jpeg_bytes([xmp_app1(hdrgm_xmp())])
    secondary = jpeg_bytes([xmp_app1(apple_aux_xmp())])
    full = primary + secondary
    path = tmp_path / "noindex.jpg"
    path.write_bytes(full)
    with FileWindow(path) as win:
        images = jpeg.walk(win)
    assert len(images) == 2
    assert images[1].offset == len(primary)


def test_multiple_app1_xmp_segments_all_collected(tmp_path):
    data = jpeg_bytes([xmp_app1(hdrgm_xmp()), xmp_app1("<x:xmpmeta>other</x:xmpmeta>")])
    with _window(tmp_path, data) as win:
        xs = jpeg.collect_xmp(jpeg.walk(win)[0])
    assert len(xs) == 2
    assert any("hdrgm:Version" in x.text for x in xs)


def test_extended_xmp_reassembled_by_guid(tmp_path):
    guid = "0" * 32
    full_note = "A" * 300
    part1 = extended_xmp_app1(guid, full_note, 0, full_note[:150].encode())
    part2 = extended_xmp_app1(guid, full_note, 150, full_note[150:].encode())
    data = jpeg_bytes([xmp_app1(hdrgm_xmp()), part1, part2])
    with _window(tmp_path, data) as win:
        xs = jpeg.collect_xmp(jpeg.walk(win)[0])
    extended = [x for x in xs if x.extended]
    assert len(extended) == 1
    assert extended[0].text == full_note


def test_iso_namespace_literal_detected(tmp_path):
    data = jpeg_bytes([iso_app2()])
    with _window(tmp_path, data) as win:
        segments = jpeg.walk(win)[0].segments
    app2 = [s for s in segments if s.marker == jpeg.APP2]
    assert app2 and app2[0].payload.startswith(b"urn:iso:std:iso:ts:21496:-1\x00")


def test_truncated_stream_raises_jpeg_error(tmp_path):
    data = jpeg_bytes([xmp_app1(hdrgm_xmp())])[:40]
    with _window(tmp_path, data) as win:
        with pytest.raises(jpeg.JpegError):
            list(jpeg.iter_segments(win, 0))


def test_no_soi_raises(tmp_path):
    with _window(tmp_path, b"not a jpeg at all") as win:
        with pytest.raises(jpeg.JpegError):
            list(jpeg.iter_segments(win, 0))
