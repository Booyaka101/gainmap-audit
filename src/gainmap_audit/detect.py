"""Classify a single file's gain map state.

Four detection rules run against every file and each records its own evidence;
the reported state is the highest-precedence rule that fired. Precedence is
ordered by how likely an arbitrary viewer is to render the HDR rendition, so
an Adobe/Google ``hdrgm`` block outranks an Apple-only auxiliary image even
when a file carries both.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import isobmff, jpeg, xmp
from .reader import FileError, FileWindow

NONE = "none"
ULTRAHDR = "ultrahdr"
ISO_JPEG = "iso-jpeg"
ISO_HEIF = "iso-heif"
APPLE_AUX = "apple-aux"
ORPHANED = "orphaned"
ERROR = "error"

STATES = (NONE, ULTRAHDR, ISO_JPEG, ISO_HEIF, APPLE_AUX, ORPHANED, ERROR)
GAIN_MAP_STATES = frozenset({ULTRAHDR, ISO_JPEG, ISO_HEIF, APPLE_AUX})

# Highest precedence first.
_PRECEDENCE = (ULTRAHDR, ISO_JPEG, ISO_HEIF, APPLE_AUX, ORPHANED)

# How widely the HDR rendition of each flavour is actually honoured.
INTEROP_RANK = {ULTRAHDR: 3, ISO_JPEG: 3, ISO_HEIF: 3, APPLE_AUX: 2, ORPHANED: 0, NONE: 0, ERROR: 0}

ISO_NAMESPACE = b"urn:iso:std:iso:ts:21496:-1\x00"
ISO_NAMESPACE_PREFIX = b"urn:iso:std:iso:ts:21496"

JPEG_EXTENSIONS = frozenset({".jpg", ".jpeg", ".jpe", ".jfif"})
ISOBMFF_EXTENSIONS = frozenset({".heic", ".heif", ".hif", ".avif"})
IMAGE_EXTENSIONS = JPEG_EXTENSIONS | ISOBMFF_EXTENSIONS


@dataclass(frozen=True)
class Evidence:
    rule: str
    detail: str
    offset: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "detail": self.detail, "offset": self.offset}


@dataclass(frozen=True)
class GainMap:
    """Where the gain map payload lives, when the container says."""

    source: str
    offset: int | None = None
    length: int | None = None
    mime: str = ""
    item_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "offset": self.offset,
            "length": self.length,
            "mime": self.mime,
            "item_id": self.item_id,
        }


@dataclass
class FileReport:
    path: Path
    state: str
    container: str
    size: int
    evidence: tuple[Evidence, ...] = ()
    gain_map: GainMap | None = None
    error: str | None = None
    rules_fired: tuple[str, ...] = ()
    ultrahdr: str | None = field(default=None, compare=False)

    @property
    def has_gain_map(self) -> bool:
        return self.state in GAIN_MAP_STATES

    @property
    def rank(self) -> int:
        return INTEROP_RANK.get(self.state, 0)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path.as_posix(),
            "state": self.state,
            "container": self.container,
            "size": self.size,
            "rules_fired": list(self.rules_fired),
            "evidence": [e.as_dict() for e in self.evidence],
            "gain_map": self.gain_map.as_dict() if self.gain_map else None,
            "error": self.error,
        }
        if self.ultrahdr is not None:
            data["ultrahdr"] = self.ultrahdr
        return data


class _Findings:
    """Collects per-rule evidence, then resolves the reported state."""

    def __init__(self) -> None:
        self.evidence: list[Evidence] = []
        self.states: dict[str, GainMap | None] = {}

    def note(self, rule: str, detail: str, offset: int | None = None) -> None:
        self.evidence.append(Evidence(rule, detail, offset))

    def fire(
        self, state: str, detail: str, offset: int | None = None, gain_map: GainMap | None = None
    ) -> None:
        self.evidence.append(Evidence(state, detail, offset))
        if state not in self.states or gain_map is not None:
            self.states[state] = gain_map

    def resolve(
        self, container: str
    ) -> tuple[str, tuple[Evidence, ...], GainMap | None, tuple[str, ...]]:
        state = next((s for s in _PRECEDENCE if s in self.states), NONE)
        if state is NONE and not self.evidence:
            self.note(NONE, f"no gain map signal in the {container} metadata")
        fired = tuple(s for s in _PRECEDENCE if s in self.states)
        return state, tuple(self.evidence), self.states.get(state), fired


def classify(path: str | os.PathLike[str]) -> FileReport:
    """Read ``path`` once and report which gain map flavour, if any, it carries."""
    resolved = Path(path)
    try:
        window = FileWindow(resolved)
    except FileError as exc:
        return FileReport(resolved, ERROR, "unknown", 0, error=str(exc))

    with window:
        if window.size == 0:
            return FileReport(resolved, ERROR, "unknown", 0, error="empty file")
        head = window.read_at(0, 16)
        try:
            if jpeg.is_jpeg(head):
                return _classify_jpeg(window, resolved)
            if isobmff.is_isobmff(head):
                return _classify_isobmff(window, resolved)
        except (jpeg.JpegError, isobmff.BoxError) as exc:
            return FileReport(resolved, ERROR, _container_of(head), window.size, error=str(exc))
        except FileError as exc:
            return FileReport(resolved, ERROR, _container_of(head), window.size, error=str(exc))
        return FileReport(
            resolved,
            ERROR,
            "unknown",
            window.size,
            error="not a JPEG, HEIC or AVIF file",
        )


def _container_of(head: bytes) -> str:
    if jpeg.is_jpeg(head):
        return "jpeg"
    if isobmff.is_isobmff(head):
        return "isobmff"
    return "unknown"


def _classify_jpeg(window: FileWindow, path: Path) -> FileReport:
    images = jpeg.walk(window)
    primary = images[0]
    found = _Findings()

    index = jpeg.find_mpf(primary)
    if index is not None:
        found.note(
            "mpf",
            f"MPF index lists {index.count} images"
            + "".join(
                f"; image {i.index} type 0x{i.mp_type:06x} at {i.offset} ({i.size} bytes)"
                for i in index.images
            ),
            index.offset,
        )

    primary_xmp = [xmp.Xmp(p.text) for p in jpeg.collect_xmp(primary)]
    secondary_xmp = [
        (image, xmp.Xmp(packet.text))
        for image in images[1:]
        for packet in jpeg.collect_xmp(image)
    ]

    _rule_ultrahdr(found, images, primary_xmp, index)
    _rule_iso_jpeg(found, primary)
    _rule_apple_jpeg(found, secondary_xmp, index)
    _rule_orphaned(found, index, secondary_xmp)

    state, evidence, gain_map, fired = found.resolve("JPEG")
    return FileReport(path, state, "jpeg", window.size, evidence, gain_map, None, fired)


def _rule_ultrahdr(
    found: _Findings,
    images: tuple[jpeg.JpegImage, ...],
    packets: list[xmp.Xmp],
    index: jpeg.MpfIndex | None,
) -> None:
    """Rule 1: ``hdrgm:Version`` in the primary image's XMP identifies Ultra HDR.

    The claim on its own is not enough. An editor that re-encodes the primary and
    copies its XMP across leaves the metadata naming a gain map the file no longer
    carries, which is orphaned rather than Ultra HDR.
    """
    primary = images[0]
    for position, packet in enumerate(packets):
        version = packet.get(xmp.HDRGM_NS, "Version")
        if version is None:
            continue
        declared = "declared" if packet.declares(xmp.HDRGM_NS) else "undeclared"
        offset = jpeg.collect_xmp(primary)[position].offset
        claim = f'hdrgm:Version="{version}" in the primary XMP ({declared} namespace)'
        gain_map = _container_gain_map(packet, index, images)
        if gain_map is None:
            found.fire(
                ORPHANED,
                f"{claim}, but no image in the file holds the gain map it names",
                offset,
            )
        else:
            found.fire(ULTRAHDR, claim, offset, gain_map)
        item = packet.gain_map_item()
        if item is not None:
            found.note(
                "gcontainer",
                f'Container:Directory item Item:Semantic="GainMap" '
                f"Item:Mime={item.mime or 'unset'} Item:Length={item.length}",
                offset,
            )
            _check_container_length(found, item, index)
        return


def _container_gain_map(
    packet: xmp.Xmp, index: jpeg.MpfIndex | None, images: tuple[jpeg.JpegImage, ...]
) -> GainMap | None:
    """Locate the payload the primary XMP claims, or None if no image holds it."""
    item = packet.gain_map_item()
    if index is None:
        # walk() found the secondaries by scanning for the next SOI, which is what an
        # Ultra HDR file looks like once something has stripped its MPF segment.
        entry = None
        offset = images[1].offset if len(images) > 1 else None
        source = "appended"
    else:
        # An MPF entry only locates a payload if an image really parsed there. A file
        # truncated after the primary keeps the entry and loses the bytes, and an entry
        # MPF labels a thumbnail was never the gain map to begin with.
        parsed = {image.offset for image in images[1:]}
        entry = next((c for c in index.gain_map_candidates if c.offset in parsed), None)
        offset = entry.offset if entry else None
        source = "mpf"
    if offset is None:
        return None
    return GainMap(
        source=f"gcontainer+{source}" if item else source,
        offset=offset,
        length=item.length if item and item.length else (entry.size if entry else None),
        mime=item.mime if item else "image/jpeg",
    )


def _check_container_length(
    found: _Findings, item: xmp.ContainerItem, index: jpeg.MpfIndex | None
) -> None:
    if item.length is None or index is None or not index.gain_map_candidates:
        return
    secondary = index.gain_map_candidates[0]
    if item.length != secondary.size:
        found.note(
            "length-mismatch",
            f"Item:Length={item.length} but the MPF entry is {secondary.size} bytes; "
            "one of the two was rewritten without the other",
            secondary.offset,
        )


def _rule_iso_jpeg(found: _Findings, primary: jpeg.JpegImage) -> None:
    """Rule 2: an APP2 segment identified by the ISO 21496-1 namespace literal."""
    for segment in primary.segments:
        if segment.marker != jpeg.APP2:
            continue
        if segment.payload.startswith(ISO_NAMESPACE):
            found.fire(
                ISO_JPEG,
                "APP2 identifier urn:iso:std:iso:ts:21496:-1 "
                f"({len(segment.payload) - len(ISO_NAMESPACE)} bytes of ISO 21496-1 metadata)",
                segment.payload_offset,
                GainMap(source="iso-app2", offset=segment.payload_offset),
            )
            return
        if segment.payload.startswith(ISO_NAMESPACE_PREFIX):
            found.fire(
                ISO_JPEG,
                "APP2 identifier urn:iso:std:iso:ts:21496 (pre-final prefix, not the "
                "full :-1 literal)",
                segment.payload_offset,
                GainMap(source="iso-app2-prefix", offset=segment.payload_offset),
            )
            return


def _rule_apple_jpeg(
    found: _Findings,
    secondary_xmp: list[tuple[jpeg.JpegImage, xmp.Xmp]],
    index: jpeg.MpfIndex | None,
) -> None:
    """Rule 4, JPEG half: an MPF secondary whose XMP names the Apple gain map URN."""
    for image, packet in secondary_xmp:
        aux_type = packet.get(xmp.APDI_NS, "AuxiliaryImageType")
        gain_map_version = packet.get(xmp.HDRGAINMAP_NS, "HDRGainMapVersion")
        if aux_type == isobmff.APPLE_GAIN_MAP_AUX:
            detail = f"apdi:AuxiliaryImageType={aux_type} in MPF image {image.index}"
        elif gain_map_version is not None:
            detail = (
                f"HDRGainMap:HDRGainMapVersion={gain_map_version} in MPF image {image.index}"
            )
        else:
            continue
        size = next(
            (i.size for i in (index.images if index else ()) if i.index == image.index), None
        )
        found.fire(
            APPLE_AUX,
            detail,
            image.offset,
            GainMap(source="mpf-apple", offset=image.offset, length=size, mime="image/jpeg"),
        )
        return


def _rule_orphaned(
    found: _Findings,
    index: jpeg.MpfIndex | None,
    secondary_xmp: list[tuple[jpeg.JpegImage, xmp.Xmp]],
) -> None:
    """An MPF secondary survives but nothing in the primary points a viewer at it."""
    if found.states or index is None:
        return
    candidates = index.gain_map_candidates
    if not candidates:
        return
    secondary = candidates[0]
    stranded = next(
        (img for img, packet in secondary_xmp if packet.declares(xmp.HDRGM_NS)),
        None,
    )
    if stranded is not None:
        detail = (
            f"MPF image {secondary.index} still carries an hdrgm XMP packet but the "
            "primary XMP has no hdrgm:Version, so viewers render SDR"
        )
    else:
        detail = (
            f"MPF lists {index.count} images and image {secondary.index} is not a thumbnail "
            f"(type 0x{secondary.mp_type:06x}), but no gain map metadata reaches a viewer"
        )
    found.fire(
        ORPHANED,
        detail,
        secondary.offset,
        GainMap(source="mpf-orphan", offset=secondary.offset, length=secondary.size),
    )


def _classify_isobmff(window: FileWindow, path: Path) -> FileReport:
    found = _Findings()
    compatible = isobmff.brands(window)
    if compatible:
        found.note("ftyp", "brands " + ", ".join(dict.fromkeys(compatible)), 0)

    meta = isobmff.read_meta(window)
    if meta is None:
        _rule_apple_urn_scan(found, window, "no meta box could be walked")
    else:
        _rule_iso_heif(found, meta, compatible)
        _rule_apple_heif(found, meta)
        if not found.states:
            _rule_apple_urn_scan(found, window, "meta box carries no tmap item or gain map auxC")

    state, evidence, gain_map, fired = found.resolve("ISOBMFF")
    return FileReport(path, state, "isobmff", window.size, evidence, gain_map, None, fired)


def _rule_iso_heif(
    found: _Findings, meta: isobmff.MetaBox, compatible: tuple[str, ...]
) -> None:
    """Rule 3: a ``tmap`` derived item, normally linked by ``iref`` ``dimg``."""
    for item in meta.items_of_type(isobmff.TMAP_ITEM_TYPE):
        inputs = meta.references_from(item.item_id, "dimg")
        detail = f"iinf item {item.item_id} has item_type tmap"
        if inputs:
            names = ", ".join(str(i) for i in inputs)
            detail += f", iref dimg -> base and gain map items {names}"
        else:
            detail += " but no iref dimg links it to a base image"
        if isobmff.TMAP_ITEM_TYPE in compatible:
            detail += "; ftyp declares the tmap brand"
        gain_input = inputs[1] if len(inputs) > 1 else None
        found.fire(
            ISO_HEIF,
            detail,
            meta.offset,
            GainMap(source="tmap-item", item_id=gain_input or item.item_id),
        )
        return


def _rule_apple_heif(found: _Findings, meta: isobmff.MetaBox) -> None:
    """Rule 4, HEIF half: an ``auxC`` naming the Apple gain map URN.

    A file can carry more than one auxiliary image (a depth map alongside a
    gain map is common on Portrait-mode HDR photos), so the item that
    actually owns the gain-map auxC is resolved through ipco/ipma, not
    guessed as "whichever item has some auxl reference".
    """
    if isobmff.APPLE_GAIN_MAP_AUX not in meta.aux_types:
        return
    linked = meta.items_with_aux_type(isobmff.APPLE_GAIN_MAP_AUX)
    if linked:
        gain_item: int | None = linked[0]
        detail = f"ipma links item {gain_item} to auxC aux_type {isobmff.APPLE_GAIN_MAP_AUX}"
    else:
        # No ipma association resolved (writer omitted it, or it didn't parse):
        # fall back to the first item with any outbound auxl reference at all.
        fallback = [
            item.item_id for item in meta.items if meta.references_from(item.item_id, "auxl")
        ]
        gain_item = fallback[0] if fallback else None
        detail = f"auxC aux_type {isobmff.APPLE_GAIN_MAP_AUX} under iprp>ipco, no ipma association"
        if gain_item is not None:
            detail += f"; guessing item {gain_item} from its auxl reference"
    base_items = meta.references_from(gain_item, "auxl") if gain_item is not None else ()
    if base_items:
        detail += f"; iref auxl -> base item {base_items[0]}"
    elif linked:
        detail += "; no iref auxl links it to a base item"
    found.fire(
        APPLE_AUX,
        detail,
        meta.offset,
        GainMap(source="auxc-item", item_id=gain_item),
    )


def _rule_apple_urn_scan(found: _Findings, window: FileWindow, why: str) -> None:
    """Literal fallback for layouts the box walker cannot make sense of."""
    hit = isobmff.scan_meta_for(window, isobmff.APPLE_GAIN_MAP_AUX.encode("ascii"))
    if hit == -1:
        found.note("urn-scan", f"{why}; the Apple gain map URN is absent too")
        return
    found.fire(
        APPLE_AUX,
        f"{why}; found the literal {isobmff.APPLE_GAIN_MAP_AUX} by byte scan",
        hit,
        GainMap(source="urn-scan", offset=hit),
    )
    found.note("urn-scan", "state came from a literal byte scan, not a parsed box tree", hit)
