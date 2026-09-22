"""Match source files to their exports across an editing round trip.

An export commonly changes extension (HEIC -> JPG -> AVIF) and Windows
filesystems are case-insensitive, so pairing is done on the file stem, not
the full name, compared case-insensitively.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from . import detect
from .detect import FileReport

SIDECAR_EXTENSIONS = frozenset({".xmp", ".aae"})
SKIP_DIR_NAMES = frozenset({"@eaDir", "@__thumb", ".thumbnails", "$RECYCLE.BIN"})

Matcher = Callable[[Path, Path], str]

OK = "ok"
STRIPPED = "stripped"
ORPHANED = "orphaned"
DEGRADED = "degraded"
ADDED = "added"
SDR_BOTH = "sdr-both"
UNPAIRED_SOURCE = "unpaired-source"
UNPAIRED_EXPORT = "unpaired-export"
AMBIGUOUS = "ambiguous"
ERROR = "error"

VERDICTS = (
    OK,
    STRIPPED,
    ORPHANED,
    DEGRADED,
    ADDED,
    SDR_BOTH,
    UNPAIRED_SOURCE,
    UNPAIRED_EXPORT,
    AMBIGUOUS,
    ERROR,
)


@dataclass(frozen=True)
class Pair:
    stem: str
    sources: tuple[Path, ...]
    exports: tuple[Path, ...]

    @property
    def ambiguous(self) -> bool:
        return len(self.exports) > 1 and len(self.sources) >= 1

    @property
    def unpaired_source(self) -> bool:
        return bool(self.sources) and not self.exports

    @property
    def unpaired_export(self) -> bool:
        return bool(self.exports) and not self.sources


def iter_image_files(
    root: Path, recursive: bool, extensions: frozenset[str]
) -> Iterable[Path]:
    """Walk ``root`` yielding image files, skipping sidecars and known junk dirs."""
    seen_dirs: set[tuple[int, int]] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            key = _dir_key(current)
        except OSError:
            continue
        if key is not None:
            if key in seen_dirs:
                continue  # symlink loop guard
            seen_dirs.add(key)
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name in SKIP_DIR_NAMES:
                continue
            if entry.is_dir(follow_symlinks=True):
                if recursive:
                    stack.append(Path(entry.path))
                continue
            if Path(name).suffix.lower() in extensions:
                yield Path(entry.path)


def _dir_key(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino) if stat.st_ino else None


def stem_key(path: Path, _root: Path | None = None) -> str:
    return path.stem.casefold()


def relpath_key(root: Path) -> Matcher:
    def key(path: Path, base: Path) -> str:
        rel = path.relative_to(base).with_suffix("")
        return rel.as_posix().casefold()

    return key


def build_pairs(
    sources: Iterable[Path],
    exports: Iterable[Path],
    source_root: Path,
    export_root: Path,
    match: str,
) -> list[Pair]:
    """Group sources and exports that share a matching key."""
    source_key, export_key = _keyfuncs(match, source_root, export_root)
    by_key: dict[str, tuple[list[Path], list[Path]]] = {}
    for path in sources:
        by_key.setdefault(source_key(path), ([], []))[0].append(path)
    for path in exports:
        by_key.setdefault(export_key(path), ([], []))[1].append(path)
    return [
        Pair(key, tuple(sorted(srcs)), tuple(sorted(exps)))
        for key, (srcs, exps) in sorted(by_key.items())
    ]


def _keyfuncs(match: str, source_root: Path, export_root: Path) -> tuple[Matcher, Matcher]:
    if match == "relpath":
        return relpath_key(source_root), relpath_key(export_root)
    if match == "stem":
        return stem_key, stem_key
    raise ValueError(f"unknown match mode: {match!r}")


@dataclass(frozen=True)
class Diff:
    stem: str
    verdict: str
    source: FileReport | None
    export: FileReport | None
    extra_exports: tuple[FileReport, ...] = ()


def diff_pair(pair: Pair, reports: dict[Path, FileReport]) -> list[Diff]:
    """One :class:`Diff` per pair, or several for an ambiguous stem."""
    if pair.unpaired_source:
        return [Diff(pair.stem, UNPAIRED_SOURCE, reports[pair.sources[0]], None)]
    if pair.unpaired_export:
        return [Diff(pair.stem, UNPAIRED_EXPORT, None, reports[pair.exports[0]])]
    if pair.ambiguous:
        exports = tuple(reports[p] for p in pair.exports)
        return [Diff(pair.stem, AMBIGUOUS, reports[pair.sources[0]], exports[0], exports[1:])]
    source = reports[pair.sources[0]]
    export = reports[pair.exports[0]]
    return [Diff(pair.stem, verdict(source, export), source, export)]


def verdict(source: FileReport, export: FileReport) -> str:
    """Classify one (source, export) pair per the states each file already carries.

    DEGRADED is reserved for a source that never had a gain map ending up with
    an export stuck in ORPHANED: since ORPHANED means the container carries
    plumbing for a gain map (an MPF secondary) with no working pointer to it,
    that plumbing is a flavour the source did not have, appearing broken. When
    the source did carry a gain map, ORPHANED and STRIPPED keep their plain
    meaning regardless of any extension change across the pair -- an HDR HEIC
    exported to a plain JPEG with no gain map at all is STRIPPED, not DEGRADED.
    """
    if source.state == detect.ERROR or export.state == detect.ERROR:
        return ERROR

    src_has, exp_has = source.has_gain_map, export.has_gain_map

    if src_has and exp_has:
        return OK
    if export.state == detect.ORPHANED:
        return ORPHANED if src_has else DEGRADED
    if src_has:
        return STRIPPED
    if exp_has:
        return ADDED
    return SDR_BOTH
