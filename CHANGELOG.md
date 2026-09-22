# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/).

## [0.1.2] - 2026-09-22

### Fixed
- A JPEG whose primary XMP declares `hdrgm:Version` was reported as `ultrahdr`
  even when the file no longer held a gain map. Pillow, and anything else that
  re-encodes the primary while carrying the XMP across, produces exactly that
  file, so `gmaudit diff` reported those round trips as clean. The Ultra HDR
  rule now needs a payload it can point at, and reports `orphaned` when the
  metadata names a gain map nothing in the file holds. Found by running the
  round trip through real image libraries; libultrahdr's own decoder disagreed
  with `gmaudit` on seven of eight real exports before the fix and none after.
- An MPF entry whose bytes are gone no longer counts as a located gain map. A
  file truncated after its primary image kept its MPF index and still reported
  `ultrahdr` with an offset past the end of the file.
- An MPF secondary that MPF labels a thumbnail is no longer accepted as the
  payload an `hdrgm:Version` claim refers to.

### Changed
- `--verify-with-ultrahdr` uses `ultrahdr_app`'s probe mode (`-P`), which reads
  the gain map metadata without decoding and writes nothing at all. Builds older
  than libultrahdr 1.5.0 have no `-P` and report it by name, so those fall back
  to the full decode in a scratch directory as before.
- `gain_map.source` names what located the payload: `mpf`, `appended`, or those
  prefixed `gcontainer+` when the XMP container directory described it too. The
  bare `gcontainer` source is gone, since a container entry on its own never
  located anything.

### Added
- `lab/`, a real-file test environment: around fifty samples from libavif,
  Awesome-Gain-Maps and libultrahdr, round tripped through Pillow, OpenCV and
  ffmpeg, plus fresh files encoded by `ultrahdr_app`, all audited and
  cross-checked against libultrahdr. Not run by CI, and excluded from the sdist.

## [0.1.1] - 2026-09-22

### Fixed
- `--verify-with-uhdrtool` invoked `uhdrtool detect -in FILE`, a binary and
  subcommand that do not exist. libultrahdr ships `ultrahdr_app`, which takes
  `-m 1 -j FILE`, reports on stderr, and exits non-zero when there is no gain
  map. The option is now `--verify-with-ultrahdr` and speaks that interface.
  Verified against a locally built `ultrahdr_app`; the JSON field is now
  `ultrahdr` rather than `uhdrtool`.
- Cross-verification decodes in a temporary directory. `ultrahdr_app` writes
  the decoded frame to `outrgb.raw` in its working directory, so verifying a
  photo folder in place would have dropped a raw dump next to the photos.

### Added
- `.gitattributes` normalising text to LF and pinning the image corpus binary,
  so a Windows clone cannot alter the bytes the parsers are tested against.

### Changed
- `PROGRESS.md`, an internal build log, is excluded from the sdist. Tests and
  the corpus still ship, so the suite is runnable from a source release.

## [0.1.0] - 2026-09-22

Initial release.

### Added
- `gmaudit check` / `scan` / `diff` CLI subcommands with `--json`, `--csv`,
  `--fail-on`, and `--verify-with-uhdrtool` options.
- Detection of four HDR gain map flavours: Adobe/Google Ultra HDR
  (`ultrahdr`), ISO/IEC TS 21496-1 in JPEG (`iso-jpeg`) and in HEIF/AVIF
  (`iso-heif`), and Apple's auxiliary gain map (`apple-aux`).
- `orphaned` state for files whose MPF/HEIF plumbing still references a
  gain map that nothing points a viewer at.
- Filename-based pairing (`stem` or `relpath`) for auditing a source tree
  against its exports.
- Dependency-free, stdlib-only implementation; read-only, never modifies
  the files it inspects.
