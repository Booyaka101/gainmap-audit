from __future__ import annotations

import struct

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
    meta,
    pitm,
)

from gainmap_audit import isobmff as bmff
from gainmap_audit.reader import FileWindow


def _window(tmp_path, data: bytes, name: str = "x.heic") -> FileWindow:
    path = tmp_path / name
    path.write_bytes(data)
    return FileWindow(path)


def test_is_isobmff_checks_ftyp_at_offset_4():
    data = ftyp("heic", ["heic", "mif1"])
    assert bmff.is_isobmff(data[:12])
    assert not bmff.is_isobmff(b"not a box at all")


def test_brands_returns_major_then_compatible(tmp_path):
    data = ftyp("avif", ["avif", "mif1", "miaf"]) + meta([pitm(1)])
    with _window(tmp_path, data, "x.avif") as win:
        assert bmff.brands(win) == ("avif", "avif", "mif1", "miaf")


def test_iso_heif_tmap_item_with_dimg_reference(tmp_path):
    meta_children = [
        pitm(1),
        iinf([infe(1, "av01", b"Color"), infe(2, "tmap", b"GMap"), infe(3, "av01", b"GMap")]),
        iref([iref_entry("dimg", 2, [1, 3])]),
    ]
    data = isobmff_file("avif", ["avif", "mif1", "miaf"], meta_children)
    with _window(tmp_path, data, "x.avif") as win:
        m = bmff.read_meta(win)
    assert m is not None
    assert m.primary_item == 1
    tmap_items = m.items_of_type("tmap")
    assert [i.item_id for i in tmap_items] == [2]
    assert m.references_from(2, "dimg") == (1, 3)


def test_apple_auxc_gain_map_with_auxl_reference(tmp_path):
    props = ipco([auxc(bmff.APPLE_GAIN_MAP_AUX)])
    meta_children = [
        pitm(1),
        iinf([infe(1, "hvc1", b"Primary"), infe(2, "hvc1", b"Aux")]),
        iref([iref_entry("auxl", 2, [1])]),
        iprp([props]),
    ]
    data = isobmff_file("heic", ["heic", "mif1"], meta_children)
    with _window(tmp_path, data) as win:
        m = bmff.read_meta(win)
    assert m is not None
    assert m.aux_types == (bmff.APPLE_GAIN_MAP_AUX,)
    assert m.references_from(2, "auxl") == (1,)
    assert m.references_to(1, "auxl") == (2,)


def test_ipma_picks_the_right_item_among_several_aux_images(tmp_path):
    # A Portrait-mode HDR photo carries both a depth map and a gain map as
    # auxiliary images. Without resolving ipco/ipma, "first item with any
    # auxl reference" picks item 2 (depth), not item 3 (the real gain map).
    depth_type = "urn:mpeg:mpegB:cicp:aux:disparity"
    props = ipco([auxc(depth_type), auxc(bmff.APPLE_GAIN_MAP_AUX)])
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
    with _window(tmp_path, data) as win:
        m = bmff.read_meta(win)
    assert m is not None
    assert m.items_with_aux_type(depth_type) == (2,)
    assert m.items_with_aux_type(bmff.APPLE_GAIN_MAP_AUX) == (3,)


def test_items_with_aux_type_empty_without_ipma(tmp_path):
    props = ipco([auxc(bmff.APPLE_GAIN_MAP_AUX)])
    data = isobmff_file(
        "heic", ["heic", "mif1"], [pitm(1), iinf([infe(1, "hvc1", b"")]), iprp([props])]
    )
    with _window(tmp_path, data) as win:
        m = bmff.read_meta(win)
    assert m is not None
    assert m.aux_types == (bmff.APPLE_GAIN_MAP_AUX,)
    assert m.items_with_aux_type(bmff.APPLE_GAIN_MAP_AUX) == ()


def test_no_meta_box_returns_none(tmp_path):
    data = ftyp("heic", ["heic", "mif1"]) + box("mdat", b"\x00" * 16)
    with _window(tmp_path, data) as win:
        assert bmff.read_meta(win) is None


def test_truncated_meta_box_does_not_raise(tmp_path):
    data = isobmff_file("heic", ["heic", "mif1"], [pitm(1), iinf([infe(1, "hvc1", b"")])])
    with _window(tmp_path, data[:30]) as win:
        m = bmff.read_meta(win)
    assert m is None or isinstance(m.items, tuple)


def test_walk_descends_only_into_meta_children(tmp_path):
    data = isobmff_file("heic", ["heic", "mif1"], [pitm(1)])
    with _window(tmp_path, data) as win:
        types = [b.type for b in bmff.walk(win)]
    assert "ftyp" in types
    assert "meta" in types
    assert "pitm" in types
    assert "mdat" in types


def test_scan_meta_for_finds_literal_without_walking(tmp_path):
    needle = b"urn:com:apple:photo:2020:aux:hdrgainmap"
    props = ipco([auxc(bmff.APPLE_GAIN_MAP_AUX)])
    data = isobmff_file("heic", ["heic", "mif1"], [pitm(1), iprp([props])])
    with _window(tmp_path, data) as win:
        assert bmff.scan_meta_for(win, needle) != -1


def test_iinf_version_below_2_yields_no_item_type(tmp_path):
    # A version-1 infe has no item_type field; the parser must skip it rather
    # than misreading following bytes as a fourcc.
    payload = struct.pack(">HH", 1, 0) + b"\x00" * 4
    old_infe = box("infe", bytes([1, 0, 0, 0]) + payload)
    data = isobmff_file("heic", ["heic", "mif1"], [pitm(1), iinf([old_infe])])
    with _window(tmp_path, data) as win:
        m = bmff.read_meta(win)
    assert m is not None
    assert m.items == ()
