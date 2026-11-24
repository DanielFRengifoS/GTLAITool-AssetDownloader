# GTL AI Asset Downloader v0.3.1

## Instagram Profile Extractor — live-test corrections

- Replaced gallery-dl `--dump-json` with `--resolve-json`, which resolves intermediary child extractors from profile URLs and emits publication-level JSON.
- Fixed frozen Windows helper stdio: PyInstaller `--windowed` can set `sys.stdout` and `sys.stderr` to `None`; the helper now reconnects to inherited subprocess pipes instead of discarding output to `os.devnull`.
- Added verbose diagnostic capture for profile discovery.
- A successful process returning no parseable publications now raises an actionable error containing exit code, stdout size, JSON record count, stderr tail and a short stdout sample.
- Kept metadata-only discovery: scanning does not download media.
- Kept scope: public own posts + Reels, combined limit 50, with 6–12 second request pacing.
- Updated About page to describe profile discovery accurately.

## Validation

- Python syntax compilation: passed.
- Unit tests: 12/12 passed, including frozen-helper stdout/stderr reconnection and no-results diagnostics.
- Live Instagram profile test: pending user Windows run.
- Windows EXE build: pending user Windows run.
