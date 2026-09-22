# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/).

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
