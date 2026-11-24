# GTL AI Asset Downloader v0.3.6

## Download resilience
- Added sequential Instagram publication throttling (default 12 seconds, configurable 5–60 seconds).
- Kept gallery-dl's internal request pacing and retries.
- Added one application-level retry for transient JSON/API response failures, 429 rate limits, 5xx errors, network errors and timeouts.
- Added exponential backoff (20-second base) and `Retry-After` support when the header is exposed in gallery-dl diagnostics.
- Authentication/challenge failures are not blindly retried.
- Added an Instagram circuit breaker: confirmed rate limiting opens it after a failed single retry; repeated access/API errors open it after two consecutive failed publications.
- Remaining Instagram items are marked deferred; direct URL items continue.
- Added a Retry failed / deferred action that filters the queue to unsuccessful items only.

## Diagnostics
- gallery-dl now runs in verbose mode for publication downloads.
- Each attempt records duration, exit code, HTTP status, content type, content length, retryability, error category, Retry-After and Firefox cookie-load evidence where available.
- Sensitive cookie and authorization values are redacted before diagnostic persistence.
- Actual response bodies are not captured or logged.

## Reporting / UX
- Completion dialog and history distinguish successful assets from files saved.
- `download_results.json` records retry attempts, failure categories, deferred assets and the applied download policy.
- CSV and Markdown reports include retry and failure-category information.
- Added live status messages for waiting, retries and circuit-breaker activation.
- Added configurable delay to Settings.

## Validation
- Python syntax compilation: passed.
- Unit tests: 33/33 passed.
- Mocked retry, classification, circuit-breaker and reporting tests: added.
- Live Instagram test: pending on user's Windows machine.
- Native Windows EXE build: pending on user's Windows machine.

- Retry-After values beyond the configured 300-second in-process wait are not shortened; the app skips the automatic retry and leaves the asset failed for a later manual retry.
