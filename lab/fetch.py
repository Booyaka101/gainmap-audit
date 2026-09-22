"""Download real gain map samples into the lab cache.

Every file is real encoder or camera output from a public project's test data or
sample set. Nothing here is synthesised. Files are cached by name and skipped if
already present, so re-runs are cheap and work offline.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import sys
import urllib.request
from pathlib import Path

LAB = Path(__file__).parent
SAMPLES = LAB / "samples"

RAW = "https://raw.githubusercontent.com"

# libavif test data, BSD-2-Clause. Chosen for the structural cases the
# ISOBMFF/ipma and XMP paths care about, not for pretty pictures.
LIBAVIF = f"{RAW}/AOMediaCodec/libavif/main/tests/data"
LIBAVIF_FILES = [
    # Auxiliary items with more than one target, and custom ipco properties.
    # These are the real-file version of the ipma association tests.
    "circle_auxl_two_targets.avif",
    "circle_cdsc_two_targets.avif",
    "circle_custom_properties.avif",
    # Gain map alongside an alpha auxiliary item: a real multi-auxiliary file.
    "color_grid_alpha_grid_gainmap_nogrid.avif",
    "color_grid_gainmap_different_grid.avif",
    "color_nogrid_alpha_nogrid_gainmap_grid.avif",
    # A real depth/auxiliary file, to prove depth is not read as a gain map.
    "colors-animated-8bpc-depth-exif-xmp.avif",
    # Gain map version signalling edge cases.
    "unsupported_gainmap_version.avif",
    "unsupported_gainmap_minimum_version.avif",
    "supported_gainmap_writer_version_with_extra_bytes.avif",
    "seine_sdr_gainmap_gammazero.avif",
    "seine_hdr_gainmap_wrongaltr.avif",
    # Plain gain map carriers, HEIF and JPEG.
    "seine_sdr_gainmap_srgb.avif",
    "seine_sdr_gainmap_srgb_icc.avif",
    "seine_hdr_gainmap_srgb.avif",
    "seine_hdr_gainmap_small_srgb.avif",
    "seine_sdr_gainmap_srgb.jpg",
    "seine_sdr_different_gainmap_srgb.jpg",
    # XMP edge cases: little endian, a trailing NUL, and extended (multi-APP1) XMP.
    "paris_exif_xmp_gainmap_littleendian.jpg",
    "paris_exif_xmp_icc_gainmap_bigendian.jpg",
    "paris_xmp_trailing_null.jpg",
    "paris_extended_xmp.jpg",
    # The older Apple auxiliary layout, next to the newer one already in tests/.
    "apple_gainmap_old.jpg",
    # SDR controls that must come back as `none`.
    "paris_exif_xmp_icc.jpg",
    "colors_hdr_srgb.avif",
]

# Real-world gain mapped photography, MIT. Content breadth: photos, video game
# captures, plots, test charts, medical imagery.
AWESOME = f"{RAW}/NMoroney/Awesome-Gain-Maps/main/images"
AWESOME_FILES = [
    "gain_mapped-photo-tokyo.jpg",
    "gain_mapped-photo-phi_falls.jpg",
    "gain_mapped-photo-laser_milky_way.jpg",
    "gain_mapped-photo-colorful_daisies.jpg",
    "gain_mapped-photo-airborne_by_christopher_klein.jpg",
    "gain_mapped-photo-canada_national_football_team_wc2022.jpg",
    "gain_mapped-video_games-guacamelee.jpg",
    "gain_mapped-video_games-nexuiz.jpg",
    "gain_mapped-video_games-speed_dreams.jpg",
    "gain_mapped-video_games-warsow.jpg",
    "gain_mapped-visualization-3d_scatterplot.jpg",
    "gain_mapped-visualization-matplotlib_gpx.jpg",
    "gain_mapped-test_chart-color_01.jpg",
    "gain_mapped-test_chart-color_02.jpg",
    "gain_mapped-test_chart-gray_51.jpg",
    "gain_mapped-test_chart-squares_b4_gm2.jpg",
    "gain_mapped-text-sphinx_01.jpg",
    "gain_mapped-ui-demo_app.jpg",
    "gain_mapped-procedural_art-square_flows_mona_lisa.jpg",
    "gain_mapped-medical-z-line-0a6bdc13-99b5-4287-8380-b469e7a397cf.jpg",
    "rgba_uhdr.jpg",
    # An SDR screenshot of the demo app: another control that must be `none`.
    "2503-hdr_multipage_app-01.jpg",
]

# libultrahdr's own test data, Apache-2.0. Includes the raw P010/YUV inputs the
# encoder needs, so the lab can generate genuine Ultra HDR files.
LIBUHDR = f"{RAW}/google/libultrahdr/main/tests/data"
LIBUHDR_FILES = [
    "jpeg_image.jpg",
    "minnie-320x240-yuv-icc.jpg",
]

SOURCES = [
    ("libavif", LIBAVIF, LIBAVIF_FILES),
    ("awesome-gain-maps", AWESOME, AWESOME_FILES),
    ("libultrahdr", LIBUHDR, LIBUHDR_FILES),
]


def fetch(base: str, group: str, name: str) -> tuple[str, str, int]:
    out = SAMPLES / group / name
    if out.exists() and out.stat().st_size > 0:
        return (name, "cached", out.stat().st_size)
    out.parent.mkdir(parents=True, exist_ok=True)
    url = f"{base}/{name}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gmaudit-lab"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
    except Exception as exc:  # noqa: BLE001 - report and carry on
        return (name, f"FAILED {exc}", 0)
    if body[:1] == b"<":
        return (name, "FAILED got HTML, not an image", 0)
    out.write_bytes(body)
    return (name, "downloaded", len(body))


def main() -> int:
    jobs = [(base, group, name) for group, base, names in SOURCES for name in names]
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch, *j): j for j in jobs}
        for fut in concurrent.futures.as_completed(futures):
            name, status, size = fut.result()
            group = futures[fut][1]
            if status.startswith("FAILED"):
                failures += 1
                print(f"  !! {group}/{name}: {status}")
            elif status == "downloaded":
                print(f"  +  {group}/{name} ({size:,} bytes)")

    total = sum(1 for _ in SAMPLES.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in SAMPLES.rglob("*") if f.is_file())
    print(f"\n{total} files in cache, {size / 1e6:.1f} MB, {failures} failures")

    digest = hashlib.sha256()
    for f in sorted(SAMPLES.rglob("*")):
        if f.is_file():
            digest.update(f.read_bytes())
    print(f"corpus sha256: {digest.hexdigest()[:16]}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
