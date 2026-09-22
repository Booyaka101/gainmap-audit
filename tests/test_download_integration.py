"""Network-dependent acceptance test: pull real files not committed to the
repo, verify them against a pinned sha256, and classify them.

This never runs by accident in an offline environment: any network failure
(no DNS, no route, a timeout) is treated as "skip", not "fail". Content that
downloads successfully but doesn't match its pinned hash IS a failure, since
that means the upstream sample changed underneath the documented state.
"""

from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from gainmap_audit import detect

CACHE_DIR = Path(__file__).parent / ".cache"

# (filename, url, sha256, expected state)
PINNED_FILES = [
    (
        "apple_gainmap_old.jpg",
        "https://raw.githubusercontent.com/AOMediaCodec/libavif/main/tests/data/apple_gainmap_old.jpg",
        "2e4310a0dd37a98678e057e25d936bbc4f936bb6fe17380c0ce99e58ecaa603b",
        detect.APPLE_AUX,
    ),
    (
        "seine_sdr_gainmap_srgb.avif",
        "https://raw.githubusercontent.com/AOMediaCodec/libavif/main/tests/data/seine_sdr_gainmap_srgb.avif",
        "e0ebdb2f1f44c7d901e6b5f817eb2520eace344624cab0d57aaa929d05d6d971",
        detect.ISO_HEIF,
    ),
]


def _fetch(name: str, url: str) -> bytes:
    CACHE_DIR.mkdir(exist_ok=True)
    cached = CACHE_DIR / name
    if cached.is_file():
        return cached.read_bytes()
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        pytest.skip(f"no network access to fetch {url}: {exc}")
    cached.write_bytes(data)
    return data


@pytest.mark.parametrize("name,url,sha256,expected_state", PINNED_FILES)
def test_pinned_sample_matches_hash_and_state(tmp_path, name, url, sha256, expected_state):
    data = _fetch(name, url)
    digest = hashlib.sha256(data).hexdigest()
    assert digest == sha256, (
        f"{name} content changed upstream (got {digest}); re-verify the expected "
        "state against the new file before updating the pin"
    )

    path = tmp_path / name
    path.write_bytes(data)
    report = detect.classify(path)
    assert report.state == expected_state
    assert report.error is None
