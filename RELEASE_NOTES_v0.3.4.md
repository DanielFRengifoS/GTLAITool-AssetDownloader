# GTL AI Asset Downloader v0.3.4

## Build dependency correction
- Fixed the invalid Codeberg commit-archive URL that returned HTTP 404.
- Use the official upstream development archive URL: `https://codeberg.org/mikf/gallery-dl/archive/master.tar.gz`.
- Removed the unsupported assumption that the development branch must report version 1.33.0; upstream development snapshots may retain the latest stable semantic version.
- Build scripts now use `--no-cache-dir --upgrade --force-reinstall` so an old cached or already-installed gallery-dl package cannot silently remain.
- Build log records the actual source URL and installed gallery-dl version.

## Instagram profile extractor
- Retains the two-stage profile HTML → numeric profile ID → `id:USER_ID` discovery flow.
- Retains posts + Reels merge, deduplication and the configured publication limit.
- Retains metadata-only discovery and token-redacted diagnostics.

## Validation
- Python syntax compilation: passed for all project Python files.
- Unit tests: 20/20 passed.
- Codeberg archive reachability: official upstream installation instructions specify the master archive URL; not downloaded in this environment.
- Live Instagram scan: pending.
- Windows EXE build: pending.
