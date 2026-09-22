"""Bounded random-access reads over a single open file handle.

Gain map detection never needs pixel data, so a 50 MB HEIC costs a few
kilobytes of IO. Every walker in this package shares one handle per file,
which is what makes "never open a file twice" cheap to honour.
"""

from __future__ import annotations

import os

CHUNK = 1 << 16


class FileError(Exception):
    """A file could not be read far enough to classify it."""


class FileWindow:
    """One open handle plus the reads the walkers need."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = os.fspath(path)
        try:
            self._fh = open(self.path, "rb")
        except OSError as exc:
            raise FileError(_oserr(exc)) from exc
        try:
            self.size = os.fstat(self._fh.fileno()).st_size
        except OSError as exc:
            self._fh.close()
            raise FileError(_oserr(exc)) from exc

    def read_at(self, offset: int, length: int) -> bytes:
        """Read up to ``length`` bytes at ``offset``; short reads are not an error."""
        if offset < 0 or length <= 0 or offset >= self.size:
            return b""
        try:
            self._fh.seek(offset)
            return self._fh.read(min(length, self.size - offset))
        except OSError as exc:
            raise FileError(_oserr(exc)) from exc

    def find(self, needle: bytes, start: int = 0, stop: int | None = None) -> int:
        """Chunked literal search. Returns the absolute offset or -1."""
        if not needle:
            return -1
        stop = self.size if stop is None else min(stop, self.size)
        overlap = len(needle) - 1
        pos = max(0, start)
        while pos < stop:
            block = self.read_at(pos, min(CHUNK, stop - pos) + overlap)
            if not block:
                return -1
            hit = block.find(needle)
            if hit != -1 and pos + hit + len(needle) <= stop:
                return pos + hit
            if len(block) <= overlap:
                return -1
            pos += len(block) - overlap
        return -1

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> FileWindow:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _oserr(exc: OSError) -> str:
    return exc.strerror or str(exc)
