"""The brief's round-trip proof: strip a real UltraHDR file's XMP and confirm
the classifier catches the resulting orphan (an MPF gain map with no primary
pointer to it), the exact failure mode this tool exists to catch.
"""

from __future__ import annotations

from pathlib import Path

from gainmap_audit import detect, jpeg
from gainmap_audit.reader import FileWindow

CORPUS = Path(__file__).parent / "corpus"


def test_stripping_xmp_orphans_a_real_ultrahdr_jpeg(tmp_path):
    source = CORPUS / "paris_exif_xmp_gainmap_bigendian.jpg"
    original = source.read_bytes()

    with FileWindow(source) as win:
        primary = jpeg.walk(win)[0]
    xmp_segments = primary.app_segments(jpeg.APP1, jpeg.XMP_ID) + primary.app_segments(
        jpeg.APP1, jpeg.XMP_EXT_ID
    )
    assert xmp_segments, "fixture must actually carry XMP for this test to prove anything"

    spans = sorted((s.offset, s.offset + 4 + len(s.payload)) for s in xmp_segments)
    for (_, end), (start, _) in zip(spans, spans[1:]):  # noqa: B905 (deliberate pairwise offset)
        assert end == start, "XMP segments must be contiguous so a straight slice removes them"
    stripped = original[: spans[0][0]] + original[spans[-1][1] :]
    assert len(stripped) < len(original)

    stripped_path = tmp_path / "paris_stripped.jpg"
    stripped_path.write_bytes(stripped)

    before = detect.classify(source)
    after = detect.classify(stripped_path)

    assert before.state == detect.ULTRAHDR
    assert before.has_gain_map

    assert after.state == detect.ORPHANED
    assert not after.has_gain_map
    assert any(e.rule == "mpf" for e in after.evidence), "the MPF secondary must still be there"
    assert any(e.rule == detect.ORPHANED for e in after.evidence)
