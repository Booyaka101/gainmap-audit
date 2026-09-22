# Build progress

Autonomous build log for `gainmap-audit`. Newest entry last.

## Phase 1 — core parsers and detection
- `reader.py`: bounded, read-only file windowing (`FileWindow.read_at`/`find`), never opens a file twice.
- `jpeg.py`: marker-stream walker (SOI/APPn/SOS/EOI), APP1 XMP (standard + extended/GUID-reassembled), APP2 MPF (CIPA DC-007, 16-byte MP Entry, offsets relative to the MP Header).
- `isobmff.py`: box walker for ftyp/meta/iinf/infe/iref/iprp/ipco/auxC/pitm, HEIF/AVIF item graph traversal.
- `xmp.py`: namespace-aware textual RDF/XML lookup (no DOM parser), `Xmp.declares`/`get`/`has`/`prefixes_for`/`container_items`/`gain_map_item`.
- `detect.py`: four precedence-resolved rules (`ultrahdr` > `iso-jpeg` > `iso-heif` > `apple-aux`), plus `orphaned` and `error` states.
- Status: done.

## Phase 2 — pairing, CLI, reporting
- `pairing.py`: filename pairing (`stem`/`relpath`), verdict matrix (OK/STRIPPED/ORPHANED/DEGRADED/ADDED/SDR-BOTH/UNPAIRED-*/AMBIGUOUS/ERROR), directory walk skipping sidecars/hidden/junk dirs.
- `cli.py`: `check`/`scan`/`diff` subcommands, `--json`/`--csv`/`--fail-on`/`--verify-with-uhdrtool`, exit 0/1/2.
- `report.py`: human table and CSV writers.
- Status: done.

## Phase 3 — tests
- Unit tests build real byte streams for every format (`tests/builders.py`, `tests/builders_isobmff.py`) — no mocks.
- `tests/corpus/` — 7 real sample files (licensed, sourced in `SOURCES.md`), classified against a documented expected-state table.
- `tests/test_roundtrip.py` — strips real XMP from a real Ultra HDR JPEG, proves the resulting `orphaned` classification on real bytes.
- `tests/test_download_integration.py` — two more pinned real files fetched over the network with sha256 verification, skips cleanly offline.
- `tests/test_cli.py` — exit codes, JSON schema, the brief's HEIC->JPG worked example end to end through the CLI.
- Result: 101 tests, all passing; `ruff check .` clean.
- Status: done.

## Phase 4 — packaging and docs
- `pyproject.toml` (hatchling, stdlib-only, console_script `gmaudit`), version 0.1.0.
- `README.md` with real captured CLI output (not hand-typed), install/usage/detection-rules/limitations sections, explicit "never modifies files" statement.
- `CHANGELOG.md` starting at 0.1.0, `LICENSE` (MIT), `.gitignore`, `.github/workflows/ci.yml` (ubuntu/macos/windows x py3.10-3.13).
- Verified: `python -m build --wheel` succeeds, `pip install dist/*.whl && gmaudit --version` works in a clean venv (`gmaudit 0.1.0`).
- Status: done.

## Phase 5 — final review
- Clone-detection check (difflib on AST-extracted function line ranges): `jpeg.py` vs `isobmff.py` whole-module similarity 12.2%; `_rule_iso_jpeg` vs `_rule_iso_heif` 0.0%; `_rule_apple_jpeg` vs `_rule_apple_heif` 4.3%. All well under the ~60% extract-shared-mechanism threshold — the four detection rules are genuinely different mechanisms (raw byte/XMP matching vs. parsed ISOBMFF item-graph traversal), not clones.
- Testing/build/lint bugs found and fixed during review were all in test fixtures (wrong MPF entry byte width, wrong MPF data_offset base, wrong MP type constant, one stale corpus doc note) — zero defects found in shipped production code.
- Status: done.
