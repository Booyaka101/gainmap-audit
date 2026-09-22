"""Encode fresh gain mapped files with libultrahdr, a writer that isn't ours.

The rest of the lab reads files other projects committed. This produces new ones
from raw pixels, so the corpus includes output from a current encoder rather than
whatever a project happened to check in years ago. Everything here is real
``ultrahdr_app`` output; nothing is hand-assembled.

Needs a built ``ultrahdr_app`` (GMAUDIT_ULTRAHDR or on PATH). libultrahdr ships
no release binaries, so build it:

    cmake -B build -DUHDR_BUILD_DEPS=1 && cmake --build build --config Release
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

LAB = Path(__file__).parent
OUT = LAB / "generated"

TOOL = os.environ.get("GMAUDIT_ULTRAHDR") or shutil.which("ultrahdr_app")

# One HDR source, then the encoder settings that change the file's structure:
# gain map channel count, resolution and gamma all land in the bytes we parse.
SOURCE = "awesome-gain-maps/gain_mapped-photo-tokyo.jpg"
VARIANTS = {
    "multichannel_map": ["-M", "1"],
    "singlechannel_map": ["-M", "0"],
    "downsampled_map_x4": ["-M", "0", "-s", "4"],
    "gamma_corrected_map": ["-M", "0", "-G", "2.2"],
    "realtime_preset": ["-M", "0", "-D", "0"],
}


def main() -> int:
    if TOOL is None:
        print("no ultrahdr_app; set GMAUDIT_ULTRAHDR or put it on PATH")
        return 1
    source = LAB / "samples" / SOURCE
    if not source.exists():
        print(f"missing {SOURCE}; run fetch.py first")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    scratch = OUT / "_scratch"
    scratch.mkdir(exist_ok=True)
    try:
        with Image.open(source) as im:
            width, height = im.size

        # Decode to rgba1010102 HLG, which is what the encoder wants back as -p.
        raw = scratch / "hdr.raw"
        done = _run(["-m", "1", "-j", str(source), "-O", "5", "-o", "1", "-z", str(raw)], scratch)
        if done.returncode != 0 or not raw.exists():
            print(f"could not decode {source.name}: {done.stderr.strip()[:200]}")
            return 1
        print(f"{source.name} -> {width}x{height} raw HDR, {raw.stat().st_size:,} bytes\n")

        failed = 0
        for name, extra in VARIANTS.items():
            dst = OUT / f"uhdr_app_{name}.jpg"
            done = _run(
                ["-m", "0", "-p", str(raw), "-a", "5", "-w", str(width), "-h", str(height),
                 "-t", "1", "-z", str(dst), *extra],
                scratch,
            )
            if done.returncode != 0 or not dst.exists():
                failed += 1
                print(f"  !! {name}: {(done.stderr or done.stdout).strip()[:150]}")
                continue
            # Read it straight back, so a file the encoder cannot reopen is caught here
            # rather than showing up later as a gmaudit disagreement.
            probe = _run(["-m", "1", "-j", str(dst), "-P"], scratch)
            reread = "reopens" if probe.returncode == 0 else f"UNREADABLE ({probe.returncode})"
            print(f"{name:<22} {dst.stat().st_size:>10,} bytes  {reread}")
        return 1 if failed else 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _run(args: list[str], scratch: Path) -> subprocess.CompletedProcess[str]:
    # ultrahdr_app writes its default outputs into the working directory.
    return subprocess.run(
        [TOOL, *args], capture_output=True, text=True, timeout=300, cwd=scratch
    )


if __name__ == "__main__":
    sys.exit(main())
