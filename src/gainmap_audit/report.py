"""Render classification and diff results as a table, JSON, or CSV."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from typing import Any, TextIO

from .detect import FileReport
from .pairing import Diff

_CHECK_HEADERS = ("STATE", "PATH", "SIZE", "RULES", "ERROR")
_DIFF_HEADERS = ("VERDICT", "PAIR", "STATE")


def write_check_table(reports: list[FileReport], out: TextIO) -> None:
    rows = [
        (
            r.state.upper(),
            str(r.path),
            "" if r.error else str(r.size),
            ",".join(r.rules_fired) or "-",
            r.error or "",
        )
        for r in reports
    ]
    _write_table(out, _CHECK_HEADERS, rows)
    counts = Counter(r.state for r in reports)
    out.write(_summary_line(len(reports), "file", counts) + "\n")


def write_diff_table(diffs: list[Diff], out: TextIO) -> None:
    rows = [(d.verdict.upper(), _diff_pair_label(d), _diff_state_label(d)) for d in diffs]
    _write_table(out, _DIFF_HEADERS, rows)
    counts = Counter(d.verdict for d in diffs)
    out.write(_summary_line(len(diffs), "pair", counts) + "\n")


def _diff_pair_label(d: Diff) -> str:
    src = d.source.path.name if d.source else f"(no source for {d.stem})"
    dst = d.export.path.name if d.export else f"(no export for {d.stem})"
    if d.extra_exports:
        dst += f" (+{len(d.extra_exports)} more)"
    return f"{src} -> {dst}"


def _diff_state_label(d: Diff) -> str:
    src = d.source.state if d.source else "-"
    dst = d.export.state if d.export else "-"
    return f"{src} -> {dst}"


def _write_table(out: TextIO, headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    out.write("  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip() + "\n")
    for row in rows:
        out.write("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() + "\n")


def _summary_line(total: int, noun: str, counts: Counter) -> str:
    parts = ", ".join(f"{n} {state}" for state, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    plural = noun if total == 1 else f"{noun}s"
    return f"{total} {plural}: {parts}" if parts else f"0 {plural}s"


def check_json(reports: list[FileReport]) -> dict[str, Any]:
    return {"files": [r.as_dict() for r in reports]}


def diff_json(diffs: list[Diff]) -> dict[str, Any]:
    return {
        "pairs": [
            {
                "stem": d.stem,
                "verdict": d.verdict,
                "source": d.source.as_dict() if d.source else None,
                "export": d.export.as_dict() if d.export else None,
                "extra_exports": [e.as_dict() for e in d.extra_exports],
            }
            for d in diffs
        ]
    }


def write_json(data: dict[str, Any], out: TextIO) -> None:
    json.dump(data, out, indent=2)
    out.write("\n")


def check_csv_rows(reports: list[FileReport]) -> list[list[str]]:
    header = ["path", "state", "container", "size", "rules_fired", "evidence", "error"]
    rows = [header]
    for r in reports:
        rows.append(
            [
                str(r.path),
                r.state,
                r.container,
                str(r.size),
                ";".join(r.rules_fired),
                " | ".join(f"[{e.rule}] {e.detail}" for e in r.evidence),
                r.error or "",
            ]
        )
    return rows


def diff_csv_rows(diffs: list[Diff]) -> list[list[str]]:
    header = ["stem", "verdict", "source", "source_state", "export", "export_state"]
    rows = [header]
    for d in diffs:
        rows.append(
            [
                d.stem,
                d.verdict,
                str(d.source.path) if d.source else "",
                d.source.state if d.source else "",
                str(d.export.path) if d.export else "",
                d.export.state if d.export else "",
            ]
        )
    return rows


def write_csv(rows: list[list[str]], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


def csv_string(rows: list[list[str]]) -> str:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue()
