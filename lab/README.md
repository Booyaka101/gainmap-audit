# Test lab

A real-file test environment. Not part of the package, not run by CI: it needs
network access, third-party image libraries and a locally built libultrahdr,
which is exactly why it lives outside `tests/`.

The committed corpus in `tests/corpus/` is seven small files. This is 100+, and
it is what found the `orphaned` false negative fixed in 0.1.2: an editor that
re-encodes the primary image and copies the XMP across leaves a file whose
metadata still names a gain map that is not there.

    pip install pillow opencv-python      # the "editors"
    python lab/fetch.py                   # 49 real files from libavif, Awesome-Gain-Maps, libultrahdr
    python lab/roundtrip.py               # push 8 of them through 5 real image processors
    python lab/generate.py                # encode fresh ones with ultrahdr_app
    python lab/audit.py                   # classify everything and check the invariants

`audit.py` is the acceptance run and the only script that can fail. It exits
non-zero if a verdict disagrees with libultrahdr's own decoder, if a file we call
gain mapped has no payload we can point at, if a real-editor export of a gain
mapped source comes back OK, or if an SDR control is classified as HDR.

Downloaded and generated assets are gitignored. Nothing here is synthesised:
every input is real encoder or camera output, and every export is a genuine
re-encode by Pillow, OpenCV, ffmpeg or ultrahdr_app.

## Cross-checking against libultrahdr

`generate.py` and the cross-check in `audit.py` want `ultrahdr_app`, which
libultrahdr does not ship as a release binary. Build it and point
`GMAUDIT_ULTRAHDR` at the result:

    git clone https://github.com/google/libultrahdr
    cmake -S libultrahdr -B libultrahdr/build -DUHDR_BUILD_DEPS=1
    cmake --build libultrahdr/build --config Release

`audit.py` still runs without it, just without the second opinion.
