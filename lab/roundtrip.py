"""Push real gain map files through real image processors and audit the result.

Every "editor" here is a genuine re-encode by a library people actually export
with, not a synthesised strip. The point is to check that `gmaudit diff` calls
the round trip correctly on real bytes:

  pil        Pillow open/save, the naive export. Drops all APPn metadata.
  pil_resize Pillow resize then save, closer to a real "export for web".
  cv2        OpenCV imread/imwrite. Drops metadata and re-encodes pixels.
  ffmpeg     ffmpeg transcode, what a batch script would do.
  xmp_kept   Pillow save with the primary XMP carried over but the gain map
             image itself gone. The nastiest case: the metadata still claims a
             gain map that no longer exists, which should read as orphaned or
             stripped rather than clean.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import cv2
from PIL import Image

LAB = Path(__file__).parent
RT = LAB / "roundtrip"
SOURCE = RT / "source"

FFMPEG = shutil.which("ffmpeg")

# Real gain map carriers, spread across writers and content types.
PICKS = [
    "awesome-gain-maps/gain_mapped-photo-tokyo.jpg",
    "awesome-gain-maps/gain_mapped-photo-phi_falls.jpg",
    "awesome-gain-maps/gain_mapped-test_chart-gray_51.jpg",
    "awesome-gain-maps/gain_mapped-video_games-warsow.jpg",
    "awesome-gain-maps/gain_mapped-visualization-matplotlib_gpx.jpg",
    "libavif/seine_sdr_gainmap_srgb.jpg",
    "libavif/paris_exif_xmp_gainmap_littleendian.jpg",
    "libavif/apple_gainmap_old.jpg",
]


def stage_sources() -> list[Path]:
    SOURCE.mkdir(parents=True, exist_ok=True)
    staged = []
    for rel in PICKS:
        src = LAB / "samples" / rel
        if not src.exists():
            print(f"  !! missing sample: {rel}")
            continue
        dst = SOURCE / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
        staged.append(dst)
    return staged


def export_pil(src: Path, dst: Path) -> None:
    with Image.open(src) as im:
        im.convert("RGB").save(dst, "JPEG", quality=92)


def export_pil_resize(src: Path, dst: Path) -> None:
    with Image.open(src) as im:
        w, h = im.size
        small = im.convert("RGB").resize((max(1, w // 2), max(1, h // 2)), Image.LANCZOS)
        small.save(dst, "JPEG", quality=88)


def export_cv2(src: Path, dst: Path) -> None:
    img = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("opencv could not read it")
    if not cv2.imwrite(str(dst), img, [cv2.IMWRITE_JPEG_QUALITY, 92]):
        raise RuntimeError("opencv could not write it")


def export_ffmpeg(src: Path, dst: Path) -> None:
    if FFMPEG is None:
        raise RuntimeError("ffmpeg not on PATH")
    proc = subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-i", str(src), "-q:v", "2", str(dst)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exit {proc.returncode}: {proc.stderr.strip()[:120]}")


def export_xmp_kept(src: Path, dst: Path) -> None:
    """Re-encode the primary only, but carry the original primary XMP across."""
    raw = src.read_bytes()
    marker = b"http://ns.adobe.com/xap/1.0/\x00"
    start = raw.find(marker)
    xmp = b""
    if start != -1:
        seg_len = int.from_bytes(raw[start - 2 : start], "big")
        xmp = raw[start + len(marker) : start - 2 + seg_len]
    with Image.open(src) as im:
        rgb = im.convert("RGB")
        if xmp:
            rgb.save(dst, "JPEG", quality=92, xmp=xmp)
        else:
            rgb.save(dst, "JPEG", quality=92)


EDITORS = {
    "pil": export_pil,
    "pil_resize": export_pil_resize,
    "cv2": export_cv2,
    "ffmpeg": export_ffmpeg,
    "xmp_kept": export_xmp_kept,
}


def main() -> int:
    sources = stage_sources()
    if not sources:
        print("no sources staged")
        return 1
    print(f"staged {len(sources)} real gain map sources\n")

    for name, fn in EDITORS.items():
        out = RT / f"export_{name}"
        out.mkdir(parents=True, exist_ok=True)
        ok = failed = 0
        for src in sources:
            dst = out / src.name
            try:
                fn(src, dst)
                ok += 1
            except Exception as exc:  # noqa: BLE001 - the report is the product
                failed += 1
                print(f"  !! {name}/{src.name}: {exc}")
        print(f"{name:<12} {ok} exported, {failed} failed -> {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
