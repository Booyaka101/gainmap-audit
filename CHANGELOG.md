# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/).

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
