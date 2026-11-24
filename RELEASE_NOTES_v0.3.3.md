# GTL AI Asset Downloader v0.3.3

## Instagram extractor redesign

### Independent profile identification
- Fetch the public profile HTML independently with HTTPX.
- Extract the numeric Instagram user ID from known profile bootstrap markers or embedded JSON objects whose username matches the requested profile.
- Do not use generic IDs from unrelated suggested users or post metadata.
- Stop with an explicit diagnostic if Instagram serves a login/challenge page or the public page no longer exposes a reliable ID.
- Pass the numeric ID to gallery-dl using its supported `https://www.instagram.com/id:USER_ID/` profile URL form. This bypasses the fragile username-to-ID GraphQL lookup stage.

### Updated upstream extractor
- Pin gallery-dl to the official 2026-11-23 nightly source commit `ceeb5bc6a402d3528ec58014ac379e0acfa95f1b`.
- This development line includes the upstream dynamic Relay-parameter collection change (`c0e3747a44`) addressing Instagram's new Polaris Relay variables.
- Require gallery-dl 1.33.0-dev or newer; stable 1.32.21 is deliberately rejected for profile discovery.
- Keep metadata-only `--resolve-json` discovery; no media is downloaded during scanning.

### Parser and diagnostics
- Parse both pretty-printed JSON documents and newline-delimited JSON.
- Redact `lsd`, `fb_dtsg`, `csrf_token`, and `sessionid` values before saving or displaying extractor logs.
- Report profile-identification success separately from posts/Reels extraction failures.
- Include the pinned upstream commit in discovery metadata.

## Validation
- Python syntax compilation: passed for all project Python files.
- Unit tests: 20/20 passed, including profile-ID extraction, numeric-ID URL routing, pretty-printed JSON parsing, and token redaction.
- Live Instagram scan: pending; not run in this environment.
- Windows EXE build: pending; this environment cannot produce the native Windows executable.
