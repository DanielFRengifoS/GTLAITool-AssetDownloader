# GTL AI Asset Downloader v0.3.5

## Firefox browser-session integration

- Added explicit Instagram authentication settings: Firefox browser session (default for this release) or anonymous public access.
- Added local Firefox profile discovery from `%APPDATA%\\Mozilla\\Firefox\\profiles.ini`.
- Supports selecting a detected Firefox profile or letting gallery-dl auto-detect its default profile.
- Uses gallery-dl's documented `--cookies-from-browser firefox/instagram.com[:PROFILE]` option, limiting imported cookies to Instagram's domain.
- Applies the same local browser-cookie source to profile metadata discovery and individual Instagram post/Reel downloads.
- No cookie values are exported, copied into settings, written to logs, or sent to GTL AI services.
- Profile names and profile directory paths are stored locally in `settings.json`; the cookies remain in Firefox's own `cookies.sqlite`.
- If a selected profile is missing or no longer has `cookies.sqlite`, the app stops with an actionable error.
- If Firefox holds a lock on the cookie database, close Firefox completely and retry.

## Fixes prompted by v0.3.4 diagnostics

- Removed the unsafe generic HTML `profile_id`/`user_id` regex extraction that accepted `profile_id=0`.
- Removed the synthetic `https://www.instagram.com/id:0/` URL path.
- Profile discovery now passes the exact username URL supplied by the user to gallery-dl.
- No profile ID is claimed unless gallery-dl itself returns usable publication metadata.
- Diagnostics distinguish HTTP 401/authentication problems from Instagram posts/Reels GraphQL operation failures.
- Expanded redaction for session cookies, CSRF values, and Cookie/Authorization headers.

## Important upstream limitation

Firefox cookies improve the authentication context and can resolve failures caused by anonymous requests or an incorrect browser session. They do not guarantee that Instagram's posts/Reels GraphQL endpoints will work. Upstream issue #9816 documents posts/Reels GraphQL failures even with valid authenticated cookies; the app reports these separately and does not retry aggressively.

## Validation
- Python syntax compilation: passed for all project Python files.
- Unit tests: 24/24 passed.
- Firefox profile discovery: unit-tested using synthetic `profiles.ini` fixtures; not yet tested against the user's Windows machine.
- Live Instagram scan with Firefox cookies: pending.
- Windows EXE build: pending.
