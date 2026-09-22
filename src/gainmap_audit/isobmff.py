"""ISOBMFF box walking for HEIC and AVIF.

Only the ``meta`` box is ever descended into. ``mdat`` payloads are larger
than the read cap by construction, so a 3 GB video-length HEIC costs the same
few kilobytes of IO as a small one.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass, field

from .reader import FileWindow

MAX_PAYLOAD = 1 << 20

# Boxes whose payload is a plain sequence of child boxes.
_PLAIN_CONTAINERS = frozenset({"iprp", "ipco", "grpl", "dinf", "sinf", "ipro"})
# FullBox containers: 4 bytes of version/flags before the children.
_FULL_CONTAINERS = frozenset({"meta"})

_ISOBMFF_BRANDS = frozenset(
    {"heic", "heix", "heim", "heis", "hevc", "hevx", "mif1", "msf1", "avif", "avis", "mif2"}
)

APPLE_GAIN_MAP_AUX = "urn:com:apple:photo:2020:aux:hdrgainmap"
TMAP_ITEM_TYPE = "tmap"


class BoxError(Exception):
    """The byte stream is not an ISOBMFF file we can walk."""


@dataclass(frozen=True)
class Box:
    type: str
    offset: int  # offset of the 32-bit size field
    size: int
    header_size: int
    payload: bytes
    truncated: bool  # payload exceeded MAX_PAYLOAD and was not read

    @property
    def body_offset(self) -> int:
        return self.offset + self.header_size


@dataclass(frozen=True)
class ItemInfo:
    item_id: int
    item_type: str
    name: str


@dataclass(frozen=True)
class ItemReference:
    type: str
    from_id: int
    to_ids: tuple[int, ...]


@dataclass(frozen=True)
class MetaBox:
    offset: int
    primary_item: int | None
    items: tuple[ItemInfo, ...]
    references: tuple[ItemReference, ...]
    aux_types: tuple[str, ...]
    # aux_type string -> item_ids that carry it via a real ipco/ipma association,
    # in ipma entry order. Empty when the file has no ipma (or it didn't parse).
    aux_type_items: dict[str, tuple[int, ...]] = field(default_factory=dict)

    def items_of_type(self, item_type: str) -> tuple[ItemInfo, ...]:
        return tuple(i for i in self.items if i.item_type == item_type)

    def references_from(self, item_id: int, ref_type: str) -> tuple[int, ...]:
        for ref in self.references:
            if ref.from_id == item_id and ref.type == ref_type:
                return ref.to_ids
        return ()

    def references_to(self, item_id: int, ref_type: str) -> tuple[int, ...]:
        return tuple(
            r.from_id for r in self.references if r.type == ref_type and item_id in r.to_ids
        )

    def items_with_aux_type(self, aux_type: str) -> tuple[int, ...]:
        """Items whose ipco/ipma association actually declares this auxC type.

        Distinct from checking ``aux_type in aux_types``, which only says the
        type exists *somewhere* under iprp>ipco, not which item it belongs to.
        """
        return self.aux_type_items.get(aux_type, ())


def is_isobmff(head: bytes) -> bool:
    return len(head) >= 12 and head[4:8] == b"ftyp"


def brands(win: FileWindow) -> tuple[str, ...]:
    """The major brand followed by every compatible brand in ``ftyp``."""
    for box in iter_boxes(win, 0, win.size):
        if box.type != "ftyp":
            continue
        body = box.payload
        found = [_fourcc(body[0:4])]
        found.extend(_fourcc(body[i : i + 4]) for i in range(8, len(body) - 3, 4))
        return tuple(b for b in found if b)
    return ()


def iter_boxes(win: FileWindow, start: int, end: int) -> Iterator[Box]:
    """Yield the boxes directly between ``start`` and ``end``."""
    pos = start
    while pos + 8 <= end:
        header = win.read_at(pos, 16)
        if len(header) < 8:
            return
        size = struct.unpack(">I", header[:4])[0]
        box_type = _fourcc(header[4:8])
        header_size = 8
        if size == 1:
            if len(header) < 16:
                return
            size = struct.unpack(">Q", header[8:16])[0]
            header_size = 16
        elif size == 0:
            size = end - pos
        if size < header_size or pos + size > end:
            return
        body_length = size - header_size
        truncated = body_length > MAX_PAYLOAD
        payload = b"" if truncated else win.read_at(pos + header_size, body_length)
        yield Box(box_type, pos, size, header_size, payload, truncated)
        pos += size


def walk(win: FileWindow, start: int = 0, end: int | None = None, depth: int = 0) -> Iterator[Box]:
    """Depth-first walk, descending into container boxes only."""
    end = win.size if end is None else end
    for box in iter_boxes(win, start, end):
        yield box
        child_start = _children_start(win, box)
        if child_start is not None and depth < 8:
            yield from walk(win, child_start, box.offset + box.size, depth + 1)


def _children_start(win: FileWindow, box: Box) -> int | None:
    if box.type in _PLAIN_CONTAINERS:
        return box.body_offset
    if box.type not in _FULL_CONTAINERS:
        return None
    # A handful of writers emit ``meta`` as a plain box; fall back when the
    # FullBox reading does not land on a valid child.
    for skip in (4, 0):
        start = box.body_offset + skip
        if _looks_like_box(win, start, box.offset + box.size):
            return start
    return box.body_offset + 4


def _looks_like_box(win: FileWindow, offset: int, end: int) -> bool:
    header = win.read_at(offset, 8)
    if len(header) < 8:
        return False
    size = struct.unpack(">I", header[:4])[0]
    if size in (0, 1):
        return True
    return 8 <= size <= end - offset and _fourcc(header[4:8]) != ""


def read_meta(win: FileWindow) -> MetaBox | None:
    """Parse the top-level ``meta`` box into the item graph the classifier needs."""
    meta = next((b for b in iter_boxes(win, 0, win.size) if b.type == "meta"), None)
    if meta is None:
        return None
    start = _children_start(win, meta)
    if start is None:
        return None
    end = meta.offset + meta.size

    primary: int | None = None
    items: tuple[ItemInfo, ...] = ()
    references: tuple[ItemReference, ...] = ()
    aux_types: list[str] = []
    properties: tuple[Box, ...] = ()
    associations: dict[int, tuple[int, ...]] = {}

    for box in walk(win, start, end, depth=1):
        if box.type == "pitm":
            primary = _parse_pitm(box.payload)
        elif box.type == "iinf":
            items = _parse_iinf(box.payload)
        elif box.type == "iref":
            references = _parse_iref(box.payload)
        elif box.type == "auxC":
            aux = _parse_auxc(box.payload)
            if aux:
                aux_types.append(aux)
        elif box.type == "ipco":
            # Direct children only, in file order: ipma property_index is
            # 1-based into exactly this sequence (ISO/IEC 14496-12 8.11.14).
            properties = tuple(iter_boxes(win, box.body_offset, box.offset + box.size))
        elif box.type == "ipma":
            associations.update(_parse_ipma(box.payload))

    aux_type_items = _resolve_aux_associations(properties, associations)
    return MetaBox(
        meta.offset,
        primary,
        items,
        references,
        tuple(aux_types),
        aux_type_items,
    )


def _resolve_aux_associations(
    properties: tuple[Box, ...], associations: dict[int, tuple[int, ...]]
) -> dict[str, tuple[int, ...]]:
    result: dict[str, list[int]] = {}
    for item_id, indices in associations.items():
        for index in indices:
            if not 1 <= index <= len(properties):
                continue
            prop = properties[index - 1]
            if prop.type != "auxC":
                continue
            aux_type = _parse_auxc(prop.payload)
            if aux_type:
                result.setdefault(aux_type, []).append(item_id)
    return {aux_type: tuple(item_ids) for aux_type, item_ids in result.items()}


def _parse_ipma(payload: bytes) -> dict[int, tuple[int, ...]]:
    """ItemPropertyAssociationBox: item_id -> the 1-based indices of its properties."""
    if len(payload) < 8:
        return {}
    version = payload[0]
    flags = int.from_bytes(payload[1:4], "big")
    (entry_count,) = struct.unpack(">I", payload[4:8])
    id_width = 2 if version < 1 else 4
    index_width = 2 if flags & 1 else 1
    index_mask = 0x7FFF if flags & 1 else 0x7F

    result: dict[int, tuple[int, ...]] = {}
    pos = 8
    for _ in range(entry_count):
        if pos + id_width + 1 > len(payload):
            break
        item_id = int.from_bytes(payload[pos : pos + id_width], "big")
        pos += id_width
        assoc_count = payload[pos]
        pos += 1
        indices = []
        for _ in range(assoc_count):
            if pos + index_width > len(payload):
                break
            raw = int.from_bytes(payload[pos : pos + index_width], "big")
            pos += index_width
            indices.append(raw & index_mask)
        result[item_id] = tuple(indices)
    return result


def _parse_pitm(payload: bytes) -> int | None:
    if len(payload) < 6:
        return None
    width = 2 if payload[0] == 0 else 4
    return int.from_bytes(payload[4 : 4 + width], "big")


def _parse_iinf(payload: bytes) -> tuple[ItemInfo, ...]:
    if len(payload) < 6:
        return ()
    version = payload[0]
    pos = 4
    if version == 0:
        count = struct.unpack(">H", payload[pos : pos + 2])[0]
        pos += 2
    else:
        count = struct.unpack(">I", payload[pos : pos + 4])[0]
        pos += 4

    entries = []
    for _ in range(count):
        if pos + 12 > len(payload):
            break
        size = struct.unpack(">I", payload[pos : pos + 4])[0]
        if _fourcc(payload[pos + 4 : pos + 8]) != "infe" or size < 12:
            break
        entry = _parse_infe(payload[pos : pos + size])
        if entry is not None:
            entries.append(entry)
        pos += size
    return tuple(entries)


def _parse_infe(box: bytes) -> ItemInfo | None:
    version = box[8]
    if version < 2:  # pre-HEIF revision, no item_type field
        return None
    id_width = 2 if version == 2 else 4
    pos = 12
    if pos + id_width + 6 > len(box):
        return None
    item_id = int.from_bytes(box[pos : pos + id_width], "big")
    pos += id_width + 2  # skip protection_index
    item_type = _fourcc(box[pos : pos + 4])
    pos += 4
    name = box[pos:].split(b"\x00", 1)[0].decode("utf-8", "replace")
    return ItemInfo(item_id, item_type, name)


def _parse_iref(payload: bytes) -> tuple[ItemReference, ...]:
    if len(payload) < 4:
        return ()
    id_width = 2 if payload[0] == 0 else 4
    pos = 4
    refs = []
    while pos + 8 <= len(payload):
        size = struct.unpack(">I", payload[pos : pos + 4])[0]
        ref_type = _fourcc(payload[pos + 4 : pos + 8])
        if size < 8 + id_width + 2 or pos + size > len(payload):
            break
        cursor = pos + 8
        from_id = int.from_bytes(payload[cursor : cursor + id_width], "big")
        cursor += id_width
        count = struct.unpack(">H", payload[cursor : cursor + 2])[0]
        cursor += 2
        to_ids = []
        for _ in range(count):
            if cursor + id_width > pos + size:
                break
            to_ids.append(int.from_bytes(payload[cursor : cursor + id_width], "big"))
            cursor += id_width
        refs.append(ItemReference(ref_type, from_id, tuple(to_ids)))
        pos += size
    return tuple(refs)


def _parse_auxc(payload: bytes) -> str:
    if len(payload) < 4:
        return ""
    return payload[4:].split(b"\x00", 1)[0].decode("utf-8", "replace")


def scan_meta_for(win: FileWindow, needle: bytes) -> int:
    """Literal search inside the ``meta`` box, for files whose layout we cannot walk."""
    meta = next((b for b in iter_boxes(win, 0, win.size) if b.type == "meta"), None)
    if meta is None:
        return win.find(needle, 0, min(win.size, MAX_PAYLOAD))
    return win.find(needle, meta.offset, meta.offset + meta.size)


def _fourcc(raw: bytes) -> str:
    if len(raw) != 4 or any(b < 0x20 or b > 0x7E for b in raw):
        return ""
    return raw.decode("ascii")


__all__ = [
    "APPLE_GAIN_MAP_AUX",
    "TMAP_ITEM_TYPE",
    "Box",
    "BoxError",
    "ItemInfo",
    "ItemReference",
    "MetaBox",
    "brands",
    "is_isobmff",
    "iter_boxes",
    "read_meta",
    "scan_meta_for",
    "walk",
]
