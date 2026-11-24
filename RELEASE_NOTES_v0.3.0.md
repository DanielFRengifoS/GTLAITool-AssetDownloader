# GTL AI Asset Downloader v0.3.0

## Instagram Profile Extractor

- Added a dedicated Profile Extractor input mode.
- Accepts a public Instagram profile URL.
- Discovers posts and Reels separately using gallery-dl metadata-only (`--dump-json`) mode.
- Merges both streams, deduplicates by shortcode, sorts by available timestamps, and applies a combined publication limit (default 50).
- Shows discovered publication type, shortcode, date, media count when available, and caption excerpt.
- Lets the user select publications and add them to the existing download queue.
- Discovery does not download media. Downloads continue through the existing queue and packaging pipeline.
- Uses conservative 6–12 second request pacing.
- Explicitly reports access/rate-limit/format failures instead of bypassing restrictions.

## Compatibility

- Existing individual Instagram publication, direct URL, and manifest workflows remain available.
- Manifest schema remains version 1.0.
- gallery-dl minimum dependency raised to 1.32.0.
- Added mocked discovery unit tests; no real Instagram account is needed for the test suite.

## Validation performed

- Python syntax compilation: passed.
- Unit tests: 9/9 passed.
- ZIP integrity: checked after packaging.
- Real Instagram discovery and Windows EXE build: not performed in this Linux environment.
