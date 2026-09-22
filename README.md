# gainmap-audit

Find photos whose HDR gain map was lost in an editing round trip.

An HDR gain map is the extra metadata that lets a JPEG or HEIC render bright
highlights on an HDR display while staying a normal SDR image everywhere
else (Apple's "HDR photo" format, Google's Ultra HDR, and the ISO 21496-1
standard all work this way). It is exactly the kind of thing an editor,
resizer, or cloud photo library silently drops: the photo still opens fine,
it just renders flat everywhere from then on. `gmaudit` finds those photos
before you notice months later that your library went dark.

**`gmaudit` never writes to your images.** It opens files read-only, parses
container metadata, and reports what it finds. It does not decode pixels,
does not repair anything, and does not touch the filesystem except to read.
If you want to actually rebuild a stripped gain map, that's a job for
[`uhdrtool`](https://github.com/google/libultrahdr); `gmaudit` can shell out
to it for cross-verification (`--verify-with-uhdrtool`) but never invokes it
to modify a file.

## Install

```
pip install gainmap-audit
```

Requires Python 3.10+. No dependencies beyond the standard library.

## The problem, in one real run

An iPhone photo with an Apple HDR gain map, exported through an editor that
doesn't understand gain maps, comes back as a plain JPEG with the same name.
Nothing in a file browser or thumbnail flags this:

```
$ gmaudit diff photos/ export/
VERDICT   PAIR                           STATE
STRIPPED  IMG_0421.HEIC -> IMG_0421.jpg  apple-aux -> none
1 pair: 1 stripped
$ echo $?
1
```

`gmaudit` paired the files by name, classified each one, and flagged the
loss. The same photo can also come back `iso-heif -> none` (Apple began
shipping ISO 21496-1 gain maps in iOS 18) -- either way, once the source
carried a gain map and the export doesn't, `gmaudit` calls it `STRIPPED`.

## Usage

### Classify individual files

```
$ gmaudit check IMG_0421.HEIC
STATE      PATH             SIZE    RULES      ERROR
APPLE-AUX  IMG_0421.HEIC    238982  apple-aux
1 file: 1 apple-aux
```

Add `--json` for machine-readable output:

```
$ gmaudit check IMG_0421.HEIC --json
{
  "files": [
    {
      "path": "IMG_0421.HEIC",
      "state": "apple-aux",
      "container": "isobmff",
      "size": 238982,
      "rules_fired": ["apple-aux"],
      "evidence": [
        {"rule": "ftyp", "detail": "brands heic, mif1, MiHE, miaf, MiHB", "offset": 0},
        {
          "rule": "apple-aux",
          "detail": "auxC aux_type urn:com:apple:photo:2020:aux:hdrgainmap under iprp>ipco; iref auxl from item 10",
          "offset": 36
        }
      ],
      "gain_map": {"source": "auxc-item", "offset": null, "length": null, "mime": "", "item_id": 10},
      "error": null
    }
  ]
}
```

### Scan a whole folder

```
$ gmaudit scan tests/corpus
STATE      PATH                                               SIZE    RULES      ERROR
APPLE-AUX  tests\corpus\apple_gainmap_new.jpg                 50824   apple-aux
APPLE-AUX  tests\corpus\apple_hdr_sample.heic                 238982  apple-aux
NONE       tests\corpus\colors_sdr_srgb.avif                  18845   -
ULTRAHDR   tests\corpus\paris_exif_xmp_gainmap_bigendian.jpg  47579   ultrahdr
NONE       tests\corpus\sample_srgb.jpg                       154458  -
ISO-HEIF   tests\corpus\seine_sdr_gainmap_notmapbrand.avif    129769  iso-heif
ISO-JPEG   tests\corpus\small_uhdr.jpg                        39385   iso-jpeg
7 files: 2 apple-aux, 2 none, 1 ultrahdr, 1 iso-heif, 1 iso-jpeg
```

Add `-r` / `--recursive` to descend into subdirectories. Hidden directories,
`@eaDir`/`@__thumb`/`.thumbnails`/`$RECYCLE.BIN`, and sidecar files
(`.xmp`, `.aae`) are skipped automatically.

### Diff a source tree against its exports

```
$ gmaudit diff photos/ export/ -r
```

Pairs files by filename stem, case-insensitively, so an extension change
across the round trip (`.HEIC` in, `.jpg` out) still matches. Pass
`--match relpath` to pair by path relative to each root instead, if your
export keeps the folder structure but changes every filename.

Each pair gets one of:

| Verdict | Meaning |
|---|---|
| `OK` | both sides carry a gain map |
| `STRIPPED` | source had one, export doesn't |
| `ORPHANED` | source had one, export has broken/unreachable gain map plumbing |
| `DEGRADED` | source never had one, export ended up with broken gain map plumbing anyway |
| `ADDED` | export gained a gain map the source didn't have |
| `SDR-BOTH` | neither side has one |
| `UNPAIRED-SOURCE` | no matching export found |
| `UNPAIRED-EXPORT` | no matching source found |
| `AMBIGUOUS` | more than one export matched the same source |
| `ERROR` | one side couldn't be read/parsed |

### Global options

- `--json` -- emit JSON instead of the table (schema shown above; `diff` emits `{"pairs": [...]}`).
- `--csv PATH` -- also write results as CSV to `PATH`.
- `--fail-on LIST` -- comma-separated states/verdicts that make the process exit 1.
  Default: `stripped,orphaned`.
- `--verify-with-uhdrtool [PATH]` -- run `uhdrtool detect -in FILE` on every JPEG
  and print a warning to stderr wherever it disagrees with `gmaudit`'s verdict.
  Looks up `uhdrtool` on `PATH` if no argument is given.

### Exit codes

- `0` -- ran clean, nothing matched `--fail-on`.
- `1` -- at least one file/pair matched `--fail-on` (or errored, for `check`/`scan`).
- `2` -- usage error: bad flags, missing directory, unreadable arguments.

## What it detects

Four independent rules run against every file; each records its own
evidence, and the highest-precedence rule that fired wins the reported
state (`ultrahdr` > `iso-jpeg` > `iso-heif` > `apple-aux` > `orphaned`):

1. **`ultrahdr`** -- `hdrgm:Version` in the primary image's XMP (Adobe/Google
   Ultra HDR), cross-referenced against the `Container:Directory` and any
   MPF secondary image.
2. **`iso-jpeg`** -- an APP2 segment identified by the literal
   `urn:iso:std:iso:ts:21496:-1` (ISO/IEC TS 21496-1).
3. **`iso-heif`** -- a `tmap` derived item in the HEIF/AVIF `meta` box,
   normally linked to its base image by an `iref` `dimg` reference.
4. **`apple-aux`** -- an `auxC` box naming
   `urn:com:apple:photo:2020:aux:hdrgainmap` (HEIC), or an MPF secondary
   image whose own XMP carries `apdi:AuxiliaryImageType` or
   `HDRGainMap:HDRGainMapVersion` (JPEG). If the container can't be parsed
   at all, a byte-level scan for the same URN literal is used as a fallback
   (evidence rule `urn-scan`), so a file with an unusual box layout still
   gets flagged rather than silently reported clean.

A fifth state, **`orphaned`**, is not a gain map flavour but a warning: the
MPF index still lists a second image that looks like a gain map, but nothing
in the primary XMP points a viewer at it. That's what a half-completed strip
or a buggy re-encode looks like from the outside, and it's exactly the case
`--fail-on orphaned` (the default) is there to catch.

`gmaudit` never crashes on a bad file. Truncated, zero-byte, unreadable, or
non-image files are reported with `state: error` and a reason, not a
traceback.

## Testing

```
pip install -e ".[dev]"
pytest
ruff check .
```

The test suite builds its own JPEG and ISOBMFF byte streams for unit tests
(`tests/builders.py`, `tests/builders_isobmff.py`), classifies a committed
corpus of real sample files (`tests/corpus/`, sourced and licensed per
`tests/corpus/SOURCES.md`), and strips XMP from a real Ultra HDR JPEG inside
`tests/test_roundtrip.py` to prove the `orphaned` detection actually fires on
real bytes, not just synthetic ones. `tests/test_download_integration.py`
pulls two more pinned real files over the network and skips cleanly if it
can't reach them.

## Limitations

- Detects the four gain map flavours in current use. A future or
  proprietary flavour with none of these markers reports `none`, correctly
  meaning "no gain map found," not "verified absent by inspecting pixels."
- Pairing across a source and export tree is filename-based (stem or
  relative path). If an export tool renames files unpredictably, pairing
  won't find the match.
- `--verify-with-uhdrtool` only checks JPEGs, since that's what `uhdrtool`
  understands.
