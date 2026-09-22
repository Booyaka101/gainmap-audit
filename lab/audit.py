"""Run gmaudit over every real file in the lab and check the invariants.

This is the acceptance run. It classifies everything the other scripts fetched,
exported and encoded, cross-checks the JPEGs against libultrahdr's own decoder,
and fails if any of these does not hold:

  * no verdict disagrees with the reference decoder
  * every file we call gain mapped has a payload we can actually point at
  * every real-editor export of a gain mapped source is flagged, never OK
  * the SDR and depth-map controls stay `none`

Results land in reports/ as JSON and a readable summary.
"""

from __future__ import annotations

import collections
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

LAB = Path(__file__).parent
REPORTS = LAB / "reports"
SRC = LAB.parent / "src"

TOOL = os.environ.get("GMAUDIT_ULTRAHDR") or shutil.which("ultrahdr_app")

# Files whose upstream project documents them as carrying no gain map. A false
# positive here is as bad as a false negative, so they are checked by name.
CONTROLS = {
    "paris_exif_xmp_icc.jpg",
    "colors_hdr_srgb.avif",
    "2503-hdr_multipage_app-01.jpg",
    # A real depth/disparity auxiliary, which is not a gain map.
    "colors-animated-8bpc-depth-exif-xmp.avif",
}

GAIN_MAP_STATES = {"ultrahdr", "iso-jpeg", "iso-heif", "apple-aux"}


def gmaudit(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(SRC))
    return subprocess.run(
        [sys.executable, "-m", "gainmap_audit.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=900,
    )


def scan(target: Path) -> tuple[list[dict], str]:
    args = ["scan", "-r", str(target), "--json"]
    if TOOL:
        args += ["--verify-with-ultrahdr", TOOL]
    done = gmaudit(*args)
    if not done.stdout.strip():
        return [], done.stderr
    return json.loads(done.stdout)["files"], done.stderr


def check_scan(files: list[dict], stderr: str) -> list[str]:
    problems = [f"reference decoder disagreement: {line}" for line in stderr.splitlines()
                if "!! disagreement" in line]
    for f in files:
        name = Path(f["path"]).name
        gm = f["gain_map"] or {}
        if f["state"] in GAIN_MAP_STATES:
            # JPEG locates the payload by byte offset, ISOBMFF by item id.
            if gm.get("offset") is None and gm.get("item_id") is None:
                problems.append(f"{name}: {f['state']} but nothing locates the payload")
            if gm.get("offset") is not None and gm["offset"] >= f["size"]:
                problems.append(f"{name}: payload offset {gm['offset']} is past EOF")
        if name in CONTROLS and f["state"] != "none":
            problems.append(f"{name}: control file classified {f['state']}")
        if not f["evidence"]:
            problems.append(f"{name}: no evidence recorded")
    return problems


def check_roundtrips() -> tuple[list[str], list[str]]:
    """Every export of a gain mapped source must be flagged, never reported OK."""
    source = LAB / "roundtrip" / "source"
    problems, lines = [], []
    if not source.is_dir():
        return ["roundtrip/source missing; run roundtrip.py first"], lines
    for export in sorted(p for p in (LAB / "roundtrip").iterdir() if p.name.startswith("export_")):
        done = gmaudit("diff", str(source), str(export), "--json")
        pairs = json.loads(done.stdout)["pairs"] if done.stdout.strip() else []
        verdicts = collections.Counter(p["verdict"] for p in pairs)
        lines.append(f"{export.name:<22} {len(pairs):>2} pairs  {dict(verdicts)}")
        for pair in pairs:
            if pair["source"]["state"] in GAIN_MAP_STATES and pair["verdict"] == "ok":
                problems.append(
                    f"{export.name}/{Path(pair['export']['path']).name}: reported ok, but "
                    f"{pair['source']['state']} became {pair['export']['state']}"
                )
    return problems, lines


def main() -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)
    print(f"reference decoder: {TOOL or 'NOT AVAILABLE, cross-check skipped'}\n")

    problems: list[str] = []
    everything: list[dict] = []
    for group in ("samples", "roundtrip", "generated"):
        target = LAB / group
        if not target.is_dir():
            print(f"{group:<12} skipped, not present")
            continue
        files, stderr = scan(target)
        everything += files
        problems += check_scan(files, stderr)
        states = dict(sorted(collections.Counter(f["state"] for f in files).items()))
        verified = sum(1 for f in files if f.get("ultrahdr") in ("decoded", "no-gainmap"))
        print(f"{group:<12} {len(files):>3} files  {states}  cross-checked {verified}")

    print()
    rt_problems, rt_lines = check_roundtrips()
    problems += rt_problems
    for line in rt_lines:
        print(line)

    REPORTS.joinpath("audit.json").write_text(
        json.dumps({"files": everything, "problems": problems}, indent=2), encoding="utf-8"
    )
    summary = [
        f"{len(everything)} real files audited, {len(problems)} problems",
        "",
        *(f"  !! {p}" for p in problems),
    ]
    REPORTS.joinpath("audit.md").write_text("\n".join(summary) + "\n", encoding="utf-8")

    print(f"\n{len(everything)} real files audited, {len(problems)} problems")
    for p in problems:
        print(f"  !! {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
