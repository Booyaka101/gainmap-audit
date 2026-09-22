"""Byte-level builders for genuine ISOBMFF (HEIC/AVIF) box trees."""

from __future__ import annotations

import struct


def box(box_type: str, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + box_type.encode("ascii") + payload


def ftyp(major: str, compatible: list[str]) -> bytes:
    payload = major.encode("ascii") + struct.pack(">I", 0)
    payload += b"".join(b.encode("ascii") for b in compatible)
    return box("ftyp", payload)


def full_box(box_type: str, version: int, payload: bytes) -> bytes:
    return box(box_type, bytes([version, 0, 0, 0]) + payload)


def pitm(item_id: int) -> bytes:
    return full_box("pitm", 0, struct.pack(">H", item_id))


def infe(item_id: int, item_type: str, name: bytes = b"") -> bytes:
    payload = struct.pack(">HH", item_id, 0) + item_type.encode("ascii") + name + b"\x00"
    return full_box("infe", 2, payload)


def iinf(entries: list[bytes]) -> bytes:
    return full_box("iinf", 0, struct.pack(">H", len(entries)) + b"".join(entries))


def iref_entry(ref_type: str, from_id: int, to_ids: list[int]) -> bytes:
    payload = struct.pack(">HH", from_id, len(to_ids)) + b"".join(
        struct.pack(">H", i) for i in to_ids
    )
    return box(ref_type, payload)


def iref(entries: list[bytes]) -> bytes:
    return full_box("iref", 0, b"".join(entries))


def auxc(aux_type: str, subtype: bytes = b"") -> bytes:
    return full_box("auxC", 0, aux_type.encode("ascii") + b"\x00" + subtype)


def ipco(children: list[bytes]) -> bytes:
    return box("ipco", b"".join(children))


def iprp(children: list[bytes]) -> bytes:
    return box("iprp", b"".join(children))


def meta(children: list[bytes]) -> bytes:
    return full_box("meta", 0, b"".join(children))


def isobmff_file(major: str, compatible: list[str], meta_children: list[bytes]) -> bytes:
    return ftyp(major, compatible) + meta(meta_children) + box("mdat", b"\x00" * 16)
