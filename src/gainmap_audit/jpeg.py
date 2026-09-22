"""JPEG marker-stream walking, MPF index parsing and XMP reassembly.

The walker stops at SOS for each image but keeps going past the primary so
that concatenated MPF images (where every gain map in a JPEG lives) are
reachable without decoding a single pixel.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass

from .reader import FileWindow

SOI = 0xD8
EOI = 0xD9
SOS = 0xDA
APP1 = 0xE1
APP2 = 0xE2

# Markers that carry no length field.
STANDALONE = {0x01, SOI, EOI} | set(range(0xD0, 0xD8))

XMP_ID = b"http://ns.adobe.com/xap/1.0/\x00"
XMP_EXT_ID = b"http://ns.adobe.com/xmp/extension/\x00"
MPF_ID = b"MPF\x00"

# MPEntry attribute bits, MPF spec (CIPA DC-007) table 4.
_MP_TYPE_MASK = 0x00FFFFFF
_MP_REPRESENTATIVE = 1 << 29

MP_TYPE_PRIMARY = 0x030000
MP_TYPE_UNDEFINED = 0x000000
# Secondaries a gain map would never be stored as.
MP_TYPE_NON_GAINMAP = frozenset({0x010001, 0x010002, 0x020001, 0x020002, 0x020003})

_MAX_SCAN_IMAGES = 8
_MAX_EXTENDED_XMP = 1 << 24


class JpegError(Exception):
    """The byte stream is not a JPEG we can walk."""


@dataclass(frozen=True)
class Segment:
    marker: int
    offset: int  # offset of the 0xFF introducing the marker
    payload: bytes

    @property
    def payload_offset(self) -> int:
        return self.offset + 4


@dataclass(frozen=True)
class MpfImage:
    index: int
    attribute: int
    size: int
    data_offset: int  # as stored, relative to the MP index TIFF header
    offset: int  # absolute offset in the file

    @property
    def mp_type(self) -> int:
        return self.attribute & _MP_TYPE_MASK

    @property
    def is_representative(self) -> bool:
        return bool(self.attribute & _MP_REPRESENTATIVE)

    @property
    def could_be_gain_map(self) -> bool:
        """True unless MPF labels this secondary a thumbnail or a multi-frame view."""
        return self.index > 0 and self.mp_type not in MP_TYPE_NON_GAINMAP


@dataclass(frozen=True)
class MpfIndex:
    offset: int  # absolute offset of the MPF TIFF header
    big_endian: bool
    count: int
    images: tuple[MpfImage, ...]

    @property
    def gain_map_candidates(self) -> tuple[MpfImage, ...]:
        return tuple(img for img in self.images if img.could_be_gain_map)


@dataclass(frozen=True)
class XmpPacket:
    text: str
    offset: int
    extended: bool


@dataclass(frozen=True)
class JpegImage:
    """One image in the stream: the primary, then any concatenated secondaries."""

    index: int
    offset: int
    segments: tuple[Segment, ...]

    def app_segments(self, marker: int, identifier: bytes) -> tuple[Segment, ...]:
        return tuple(
            s for s in self.segments if s.marker == marker and s.payload.startswith(identifier)
        )


def is_jpeg(head: bytes) -> bool:
    return head[:3] == b"\xff\xd8\xff"


def iter_segments(win: FileWindow, start: int) -> Iterator[Segment]:
    """Yield marker segments from the SOI at ``start`` up to and including SOS."""
    if win.read_at(start, 2) != b"\xff\xd8":
        raise JpegError(f"no SOI marker at offset {start}")
    pos = start + 2
    while pos + 1 < win.size:
        lead = win.read_at(pos, 2)
        if len(lead) < 2:
            raise JpegError(f"truncated at offset {pos}")
        if lead[0] != 0xFF:
            raise JpegError(f"expected a marker at offset {pos}, found 0x{lead[0]:02x}")
        marker = lead[1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker in STANDALONE:
            yield Segment(marker, pos, b"")
            pos += 2
            continue
        raw = win.read_at(pos + 2, 2)
        if len(raw) < 2:
            raise JpegError(f"truncated segment length at offset {pos}")
        length = struct.unpack(">H", raw)[0]
        if length < 2:
            raise JpegError(f"bad segment length {length} at offset {pos}")
        payload = win.read_at(pos + 4, length - 2)
        yield Segment(marker, pos, payload)
        if marker == SOS:
            return
        pos += 2 + length
    raise JpegError("marker stream ended before SOS")


def walk(win: FileWindow) -> tuple[JpegImage, ...]:
    """Walk the primary image, then each concatenated image after it.

    MPF offsets are used when present because they are exact; otherwise the
    remainder of the file is scanned for the next SOI.
    """
    primary = JpegImage(0, 0, tuple(iter_segments(win, 0)))
    images = [primary]
    for index, offset in enumerate(_secondary_offsets(win, primary), start=1):
        try:
            images.append(JpegImage(index, offset, tuple(iter_segments(win, offset))))
        except JpegError:
            break
    return tuple(images)


def _secondary_offsets(win: FileWindow, primary: JpegImage) -> list[int]:
    index = find_mpf(primary)
    if index is not None and len(index.images) > 1:
        return [img.offset for img in index.images[1:] if 0 < img.offset < win.size]
    sos = next((s.offset for s in primary.segments if s.marker == SOS), None)
    if sos is None:
        return []
    offsets: list[int] = []
    pos = sos
    while len(offsets) < _MAX_SCAN_IMAGES:
        hit = win.find(b"\xff\xd8\xff", pos + 2)
        if hit == -1:
            break
        offsets.append(hit)
        pos = hit
    return offsets


def find_mpf(image: JpegImage) -> MpfIndex | None:
    """Parse the MP Index IFD out of the image's MPF APP2 segment."""
    for segment in image.app_segments(APP2, MPF_ID):
        parsed = parse_mpf(segment.payload, segment.payload_offset)
        if parsed is not None:
            return parsed
    return None


def parse_mpf(payload: bytes, payload_offset: int) -> MpfIndex | None:
    body = payload[len(MPF_ID) :]
    if len(body) < 8 or body[:2] not in (b"MM", b"II"):
        return None
    endian = ">" if body[:2] == b"MM" else "<"
    base = payload_offset + len(MPF_ID)
    try:
        magic, ifd_offset = struct.unpack(endian + "HI", body[2:8])
        if magic != 42:
            return None
        (entry_count,) = struct.unpack(endian + "H", body[ifd_offset : ifd_offset + 2])
    except struct.error:
        return None

    count = 0
    images: tuple[MpfImage, ...] = ()
    for i in range(entry_count):
        entry = body[ifd_offset + 2 + i * 12 :][:12]
        if len(entry) < 12:
            break
        tag, _type, value_count = struct.unpack(endian + "HHI", entry[:8])
        (value,) = struct.unpack(endian + "I", entry[8:12])
        if tag == 0xB001:
            count = value
        elif tag == 0xB002:
            images = _parse_mp_entries(body, endian, value, value_count, base)
    if not count and not images:
        return None
    return MpfIndex(base, endian == ">", count or len(images), images)


def _parse_mp_entries(
    body: bytes, endian: str, offset: int, byte_count: int, base: int
) -> tuple[MpfImage, ...]:
    entries = []
    for i in range(byte_count // 16):
        raw = body[offset + i * 16 :][:16]
        if len(raw) < 16:
            break
        attribute, size, data_offset = struct.unpack(endian + "III", raw[:12])
        absolute = 0 if data_offset == 0 else base + data_offset
        entries.append(MpfImage(i, attribute, size, data_offset, absolute))
    return tuple(entries)


def collect_xmp(image: JpegImage) -> tuple[XmpPacket, ...]:
    """Every XMP packet in the image: each standard APP1, then reassembled extensions."""
    packets = [
        XmpPacket(_decode(s.payload[len(XMP_ID) :]), s.payload_offset, False)
        for s in image.app_segments(APP1, XMP_ID)
    ]
    packets.extend(_reassemble_extended(image))
    return tuple(packets)


def _reassemble_extended(image: JpegImage) -> list[XmpPacket]:
    """Join extended-XMP chunks by GUID (XMP specification part 3, section 1.1.3.1)."""
    buffers: dict[bytes, bytearray] = {}
    starts: dict[bytes, int] = {}
    for segment in image.app_segments(APP1, XMP_EXT_ID):
        header = segment.payload[len(XMP_EXT_ID) :][:40]
        if len(header) < 40:
            continue
        guid = header[:32]
        total, offset = struct.unpack(">II", header[32:40])
        if not 0 < total <= _MAX_EXTENDED_XMP:
            continue
        chunk = segment.payload[len(XMP_EXT_ID) + 40 :]
        buffer = buffers.setdefault(guid, bytearray(total))
        starts.setdefault(guid, segment.payload_offset)
        if offset + len(chunk) <= len(buffer):
            buffer[offset : offset + len(chunk)] = chunk
    return [
        XmpPacket(_decode(bytes(buffer)), starts[guid], True) for guid, buffer in buffers.items()
    ]


def _decode(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
