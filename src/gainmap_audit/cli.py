"""``gmaudit``: read-only detection, classification and folder diffing for HDR gain maps."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__, detect, report
from .detect import FileReport
from .pairing import ERROR as PAIR_ERROR
from .pairing import build_pairs, diff_pair, iter_image_files

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

DEFAULT_FAIL_ON = ("stripped", "orphaned")
_ALL_VERDICTS = frozenset(
    {
        "stripped",
        "orphaned",
        "degraded",
        "added",
        "sdr-both",
        "unpaired-source",
        "unpaired-export",
        "ambiguous",
        "error",
    }
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE

    try:
        fail_on = _parse_fail_on(args.fail_on)
    except ValueError as exc:
        parser.error(str(exc))
        return EXIT_USAGE  # unreachable, parser.error exits

    try:
        if args.command == "check":
            return _run_check(args, fail_on)
        if args.command == "scan":
            return _run_scan(args, fail_on)
        if args.command == "diff":
            return _run_diff(args, fail_on)
    except UsageError as exc:
        print(f"gmaudit: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    parser.error(f"unknown command: {args.command}")
    return EXIT_USAGE


class UsageError(Exception):
    """A user-facing input problem: bad path, bad flag combination."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gmaudit",
        description="Find photos whose HDR gain map was lost in an editing round trip.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _add_global_options(parser)

    subparsers = parser.add_subparsers(dest="command")

    check = subparsers.add_parser("check", help="classify individual files")
    check.add_argument("files", nargs="+", help="image files to classify")
    _add_global_options(check)

    scan = subparsers.add_parser("scan", help="classify every image under a directory")
    scan.add_argument("directory", help="directory to scan")
    scan.add_argument("-r", "--recursive", action="store_true", help="recurse into subdirectories")
    _add_global_options(scan)

    diff = subparsers.add_parser("diff", help="compare a source tree against its exports")
    diff.add_argument("source_dir", help="directory of originals")
    diff.add_argument("export_dir", help="directory of exported/edited copies")
    diff.add_argument("-r", "--recursive", action="store_true", help="recurse into subdirectories")
    diff.add_argument(
        "--match",
        choices=("stem", "relpath"),
        default="stem",
        help="pairing key: filename stem (default) or path relative to each root",
    )
    _add_global_options(diff)

    return parser


def _add_global_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument("--csv", metavar="PATH", help="also write results as CSV to PATH")
    parser.add_argument(
        "--fail-on",
        default=",".join(DEFAULT_FAIL_ON),
        help="comma-separated states/verdicts that cause exit 1 "
        f"(default: {','.join(DEFAULT_FAIL_ON)})",
    )
    parser.add_argument(
        "--verify-with-ultrahdr",
        nargs="?",
        const="ultrahdr_app",
        metavar="PATH",
        help="cross-check JPEG verdicts by decoding them with libultrahdr's "
        "ultrahdr_app (default: look it up on PATH)",
    )


def _parse_fail_on(raw: str) -> frozenset[str]:
    values = frozenset(v.strip() for v in raw.split(",") if v.strip())
    unknown = values - _ALL_VERDICTS - set(detect.STATES)
    if unknown:
        raise ValueError(f"unknown --fail-on value(s): {', '.join(sorted(unknown))}")
    return values


def _run_check(args: argparse.Namespace, fail_on: frozenset[str]) -> int:
    reports = [detect.classify(f) for f in args.files]
    _attach_ultrahdr(reports, args.verify_with_ultrahdr)
    _emit(reports, args, report.write_check_table, report.check_json, report.check_csv_rows)
    return _exit_code(any(r.state in fail_on or r.state == detect.ERROR for r in reports))


def _run_scan(args: argparse.Namespace, fail_on: frozenset[str]) -> int:
    directory = _require_dir(args.directory)
    files = sorted(iter_image_files(directory, args.recursive, detect.IMAGE_EXTENSIONS))
    if not files:
        print(f"gmaudit: no image files found under {directory}", file=sys.stderr)
    reports = [detect.classify(f) for f in files]
    _attach_ultrahdr(reports, args.verify_with_ultrahdr)
    _emit(reports, args, report.write_check_table, report.check_json, report.check_csv_rows)
    return _exit_code(any(r.state in fail_on or r.state == detect.ERROR for r in reports))


def _run_diff(args: argparse.Namespace, fail_on: frozenset[str]) -> int:
    source_root = _require_dir(args.source_dir)
    export_root = _require_dir(args.export_dir)

    sources = list(iter_image_files(source_root, args.recursive, detect.IMAGE_EXTENSIONS))
    exports = list(iter_image_files(export_root, args.recursive, detect.IMAGE_EXTENSIONS))
    pairs = build_pairs(sources, exports, source_root, export_root, args.match)

    all_paths = {p for pair in pairs for p in (*pair.sources, *pair.exports)}
    reports = {path: detect.classify(path) for path in all_paths}
    _attach_ultrahdr(list(reports.values()), args.verify_with_ultrahdr)

    diffs = [d for pair in pairs for d in diff_pair(pair, reports)]
    _emit(diffs, args, report.write_diff_table, report.diff_json, report.diff_csv_rows)
    return _exit_code(any(d.verdict in fail_on or d.verdict == PAIR_ERROR for d in diffs))


def _require_dir(raw: str) -> Path:
    path = Path(raw)
    if not path.is_dir():
        raise UsageError(f"not a directory: {raw}")
    return path


def _exit_code(found_failure: bool) -> int:
    return EXIT_FINDINGS if found_failure else EXIT_OK


def _emit(items, args, table_writer, json_builder, csv_rows_builder) -> None:
    if args.json:
        report.write_json(json_builder(items), sys.stdout)
    else:
        table_writer(items, sys.stdout)
    if args.csv:
        report.write_csv(csv_rows_builder(items), args.csv)


_NO_GAINMAP = "does not contain gainmap image"


def _attach_ultrahdr(reports: list[FileReport], tool: str | None) -> None:
    """Decode each JPEG with ultrahdr_app and note where it disagrees with us."""
    if tool is None:
        return
    binary = shutil.which(tool) or (tool if Path(tool).is_file() else None)
    if binary is None:
        print(
            f"gmaudit: warning: {tool!r} not found, skipping verification",
            file=sys.stderr,
        )
        return
    targets = [r for r in reports if r.container == "jpeg" and r.state != detect.ERROR]
    if not targets:
        return
    # ultrahdr_app dumps the decoded frame as outrgb.raw into its working
    # directory, so it runs in a scratch dir rather than the user's photo folder.
    with tempfile.TemporaryDirectory(prefix="gmaudit-") as scratch:
        for r in targets:
            r.ultrahdr = _run_ultrahdr(binary, r, scratch)


def _run_ultrahdr(binary: str, r: FileReport, scratch: str) -> str:
    name = Path(binary).name
    try:
        proc = subprocess.run(
            # Absolute, because cwd is the scratch dir and not where we started.
            [binary, "-m", "1", "-j", str(r.path.resolve())],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=scratch,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error: could not run {name} ({exc})"

    lines = (proc.stderr.strip() or proc.stdout.strip()).splitlines()
    detail = lines[-1].strip() if lines else ""
    if proc.returncode == 0:
        theirs = "decoded"
    elif _NO_GAINMAP in detail:
        theirs = "no-gainmap"
    else:
        # It refused to render the file without claiming either way, e.g. a gain
        # map whose XMP is missing hdrgm:GainMapMax. Not a disagreement with us.
        return f"undecodable: {detail or f'exit {proc.returncode}'}"

    if (theirs == "decoded") != r.has_gain_map:
        print(
            f"!! disagreement: {r.path} -- gmaudit says {r.state}, {name} says {theirs}",
            file=sys.stderr,
        )
    return theirs


if __name__ == "__main__":
    sys.exit(main())
