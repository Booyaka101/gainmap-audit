"""Namespace-aware lookups over an XMP packet.

Prefixes are resolved from the packet's own ``xmlns`` declarations rather than
assumed, so a writer that binds the gain map namespace to something other than
``hdrgm`` still matches. The lookups are textual on purpose: a packet that an
editor truncated mid-write is exactly the case this tool exists to catch, and
an XML parser would simply refuse it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HDRGM_NS = "http://ns.adobe.com/hdr-gain-map/1.0/"
CONTAINER_NS = "http://ns.google.com/photos/1.0/container/"
ITEM_NS = "http://ns.google.com/photos/1.0/container/item/"
APDI_NS = "http://ns.apple.com/pixeldatainfo/1.0/"
HDRGAINMAP_NS = "http://ns.apple.com/HDRGainMap/1.0/"

_XMLNS = re.compile(r'xmlns:([A-Za-z_][\w.\-]*)\s*=\s*["\']([^"\']+)["\']')

# Fallback prefixes for writers that omit the xmlns declaration.
_DEFAULT_PREFIXES = {
    HDRGM_NS: "hdrgm",
    CONTAINER_NS: "Container",
    ITEM_NS: "Item",
    APDI_NS: "apdi",
    HDRGAINMAP_NS: "HDRGainMap",
}


@dataclass(frozen=True)
class ContainerItem:
    """One entry of the GContainer directory in the primary image's XMP."""

    semantic: str
    mime: str
    length: int | None


class Xmp:
    """One XMP packet, addressed by namespace URI and property name."""

    def __init__(self, text: str) -> None:
        self.text = text
        self._prefixes: dict[str, list[str]] = {}
        for prefix, uri in _XMLNS.findall(text):
            self._prefixes.setdefault(uri, []).append(prefix)

    def declares(self, namespace: str) -> bool:
        return namespace in self._prefixes

    def prefixes_for(self, namespace: str) -> list[str]:
        declared = self._prefixes.get(namespace, [])
        fallback = _DEFAULT_PREFIXES.get(namespace)
        if fallback and fallback not in declared:
            declared = [*declared, fallback]
        return declared

    def get(self, namespace: str, name: str) -> str | None:
        """The property value, whether written as an attribute or an element."""
        return _lookup(self.text, self.prefixes_for(namespace), name)

    def has(self, namespace: str, name: str) -> bool:
        return self.get(namespace, name) is not None

    def container_items(self) -> tuple[ContainerItem, ...]:
        """Parse ``Container:Directory`` into its ordered ``Container:Item`` entries."""
        items = []
        item_prefixes = self.prefixes_for(ITEM_NS)
        for chunk in _split_container_items(self.text, self.prefixes_for(CONTAINER_NS)):
            semantic = _lookup(chunk, item_prefixes, "Semantic")
            if semantic is None:
                continue
            mime = _lookup(chunk, item_prefixes, "Mime") or ""
            raw_length = _lookup(chunk, item_prefixes, "Length")
            length = int(raw_length) if raw_length and raw_length.isdigit() else None
            items.append(ContainerItem(semantic, mime, length))
        return tuple(items)

    def gain_map_item(self) -> ContainerItem | None:
        return next((i for i in self.container_items() if i.semantic == "GainMap"), None)


def _lookup(text: str, prefixes: list[str], name: str) -> str | None:
    for prefix in prefixes:
        qualified = re.escape(f"{prefix}:{name}")
        attribute = re.search(qualified + r'\s*=\s*["\']([^"\']*)["\']', text)
        if attribute:
            return attribute.group(1)
        element = re.search(rf"<{qualified}[^>]*>(.*?)</{qualified}>", text, re.DOTALL)
        if element:
            return element.group(1).strip()
    return None


def _split_container_items(text: str, container_prefixes: list[str]) -> list[str]:
    """Slice the packet into one chunk per directory entry.

    Writers either wrap each entry in a ``Container:Item`` element or hang the
    ``Item:`` properties straight off the ``rdf:li``; both appear in the wild.
    """
    openers = [f"<{prefix}:Item" for prefix in container_prefixes]
    openers.append("<rdf:li")
    for opener in openers:
        starts = [m.start() for m in re.finditer(re.escape(opener), text)]
        if len(starts) < 2 and opener == "<rdf:li":
            continue
        if not starts:
            continue
        bounds = [*starts, len(text)]
        return [text[bounds[i] : bounds[i + 1]] for i in range(len(starts))]
    return []
