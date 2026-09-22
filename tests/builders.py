"""Byte-level builders for genuine JPEG and ISOBMFF fixtures.

Every builder assembles real, spec-shaped bytes (real marker structure, real
XMP text, real TIFF IFD encoding, real box headers) rather than mocking the
classifier's inputs. This is what "real bytes, never mocks" means for a
format library: the fixture is a file the format actually allows, just a
minimal one.
"""

from __future__ import annotations

import struct

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"
MPF_ID = b"MPF\x00"


def app_segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def minimal_scan_data() -> bytes:
    """SOF0/DHT/SOS headers plus one MCU: a marker stream long enough to reach SOS legally.

    Not a decodable image (this project never decodes pixels), just correctly
    framed baseline-JPEG markers.
    """
    sof = bytes([0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01, 0x00, 0x01, 0x01, 0x01, 0x11, 0x00])
    dqt = bytes([0xFF, 0xDB, 0x00, 0x43, 0x00]) + bytes([1] * 64)
    dht = bytes([0xFF, 0xC4, 0x00, 0x1F, 0x00]) + bytes(16) + bytes(range(1, 13))
    sos = bytes([0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3F, 0x00])
    scan = bytes([0xAA, 0x55])  # arbitrary entropy-coded bytes
    return dqt + dht + sof + sos + scan


def jpeg_bytes(app_segments: list[bytes], trailing: bytes = b"") -> bytes:
    """A well-formed single-image JPEG: SOI, the given APPn segments, a real scan, EOI."""
    return SOI + b"".join(app_segments) + minimal_scan_data() + EOI + trailing


def xmp_app1(xml: str) -> bytes:
    return app_segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xml.encode("utf-8"))


def extended_xmp_app1(guid: str, full_text: str, offset: int, chunk: bytes) -> bytes:
    header = (
        b"http://ns.adobe.com/xmp/extension/\x00"
        + guid.encode("ascii")
        + struct.pack(">II", len(full_text.encode("utf-8")), offset)
    )
    return app_segment(0xE1, header + chunk)


def hdrgm_xmp(version: str = "1.0", with_container: bool = False) -> str:
    container = ""
    if with_container:
        container = (
            "<Container:Directory><rdf:Seq>"
            '<rdf:li rdf:parseType="Resource">'
            '<Container:Item Item:Semantic="Primary" Item:Mime="image/jpeg"/></rdf:li>'
            '<rdf:li rdf:parseType="Resource">'
            '<Container:Item Item:Semantic="GainMap" Item:Mime="image/jpeg" '
            'Item:Length="1234"/></rdf:li>'
            "</rdf:Seq></Container:Directory>"
        )
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" '
        'xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" '
        'xmlns:Container="http://ns.google.com/photos/1.0/container/" '
        'xmlns:Item="http://ns.google.com/photos/1.0/container/item/" '
        f'hdrgm:Version="{version}">'
        f"{container}"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>'
    )


def apple_aux_xmp(version: str = "131072") -> str:
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" '
        'xmlns:apdi="http://ns.apple.com/pixeldatainfo/1.0/" '
        'xmlns:HDRGainMap="http://ns.apple.com/HDRGainMap/1.0/" '
        f'HDRGainMap:HDRGainMapVersion="{version}" '
        'apdi:AuxiliaryImageType="urn:com:apple:photo:2020:aux:hdrgainmap"/>'
        "</rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>'
    )


def mpf_app2(entries: list[tuple[int, int, int]], big_endian: bool = True) -> bytes:
    """Build a real MPF APP2 segment: identifier, TIFF header, MP Index IFD.

    ``entries`` is a list of (attribute, size, data_offset) tuples; entry 0's
    data_offset is conventionally 0 (the primary image itself).
    """
    endian = ">" if big_endian else "<"
    byte_order = b"MM" if big_endian else b"II"
    tiff_header = byte_order + struct.pack(endian + "HI", 42, 8)

    count_entry = struct.pack(endian + "HHII", 0xB001, 7, 4, len(entries))
    index_entry_tag = struct.pack(endian + "HHI", 0xB002, 7, len(entries) * 16)
    # MP Entry array offset is written after we know the IFD layout below.
    ifd_entry_count = 2
    ifd_size = 2 + ifd_entry_count * 12 + 4  # count + entries + next-IFD offset
    mp_entry_array_offset = 8 + ifd_size

    index_entry = index_entry_tag + struct.pack(endian + "I", mp_entry_array_offset)
    ifd = (
        struct.pack(endian + "H", ifd_entry_count)
        + count_entry
        + index_entry
        + struct.pack(endian + "I", 0)
    )
    mp_entries = b"".join(
        struct.pack(endian + "IIIHH", attribute, size, offset, 0, 0)
        for attribute, size, offset in entries
    )
    body = tiff_header + ifd + mp_entries
    return app_segment(0xE2, MPF_ID + body)


def concat_mpf(app_segments: list[bytes], secondary_body: bytes, attribute: int = 0) -> bytes:
    """A real primary+secondary JPEG pair with a correct MPF data_offset.

    The entry's data_offset is relative to the MP Header, which only exists once
    the MPF segment has been laid out, so the primary is measured with a
    placeholder before the real segment is written. ``attribute`` sets the
    secondary's MP attribute, e.g. 0x010001 to label it a thumbnail.
    """

    def build(primary_size: int, data_offset: int) -> bytes:
        entries = [(0x030000, primary_size, 0), (attribute, len(secondary_body), data_offset)]
        return jpeg_bytes([*app_segments, mpf_app2(entries)])

    probe = build(0, 0)
    base = 2 + sum(len(s) for s in app_segments) + 4 + len(MPF_ID)
    data = build(len(probe), len(probe) - base)
    assert len(data) == len(probe)
    return data + secondary_body


def iso_app2(
    namespace: bytes = b"urn:iso:std:iso:ts:21496:-1\x00", extra: bytes = b"\x00" * 4
) -> bytes:
    return app_segment(0xE2, namespace + extra)
