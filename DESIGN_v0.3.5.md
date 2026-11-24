# GTL AI Asset Downloader v0.3.5 — Design Notes

## Objective
Use the user's existing Firefox login as a local authentication source for Instagram profile discovery and individual publication downloads, without asking for credentials or exporting cookie files.

## Architecture

### Authentication settings
- `instagram_auth_mode`: `firefox` (default for v0.3.5) or `anonymous`.
- `firefox_profile`: optional absolute path to the selected Firefox profile directory.
- The Settings page discovers profiles from `%APPDATA%\\Mozilla\\Firefox\\profiles.ini`.
- The profile picker only lists profiles that contain `cookies.sqlite`.
- Auto-detect is available when no explicit profile is selected.

### Cookie access
- gallery-dl receives `--cookies-from-browser firefox/instagram.com` or the same selector with the selected profile path.
- The domain selector limits cookie import to Instagram's domain.
- Cookie values are read by gallery-dl from Firefox locally; GTL AI does not export them to a text file, persist them in settings, or transmit them to a GTL AI service.
- The selected profile path and auth mode are local settings.
- Firefox Multi-Account Container-specific selection is not exposed in this release; gallery-dl's default browser-cookie behavior reads cookies outside containers. Users who sign in only inside a Firefox container may need a later container selector.

### Discovery
- Use the exact username URL supplied by the user for both posts and Reels extraction.
- Do not scrape generic `profile_id` or `user_id` strings from HTML.
- Do not construct a synthetic `id:0` URL.
- Pass the same selected Firefox auth source to posts and Reels metadata-only scans.
- Merge and de-duplicate publications by shortcode; apply the configured combined limit.

### Individual downloads
- Pass the same Firefox cookie selector to gallery-dl when downloading selected `/p/`, `/reel/`, or `/tv/` URLs.
- Direct media URL downloads remain unchanged and do not receive Instagram browser cookies.

### Diagnostics
- Distinguish HTTP 401/authentication failures from posts/Reels GraphQL operation failures.
- Redact session cookie values and Cookie/Authorization headers before displaying or saving diagnostics.
- Do not aggressively retry Instagram requests.

## Known limitation
A valid browser session is not a guaranteed fix for Instagram's posts/Reels API. Upstream issue #9816 documents posts/Reels GraphQL failures even when valid authenticated cookies are supplied. v0.3.5 improves local authentication and removes the known `profile_id=0` bug, but the live profile-feed query still needs to be tested on the user's machine.

## Validation
- Python syntax compilation: passed.
- Unit tests: 24/24 passed.
- Firefox `profiles.ini` parsing: covered by a synthetic fixture.
- Browser-cookie selector construction: covered by unit tests.
- Profile discovery uses the original username URL: covered by unit tests.
- Individual publication downloads receive the Firefox cookie selector: covered by a mocked subprocess test.
- Live Firefox cookie read: pending on the user's Windows machine.
- Live Instagram posts/Reels scan: pending.
- Native Windows EXE build: pending; build locally with `build_windows.bat`.
