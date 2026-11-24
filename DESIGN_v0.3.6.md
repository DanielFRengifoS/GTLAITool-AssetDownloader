# GTL AI Asset Downloader v0.3.6 — Download Resilience Design

## Objective
Improve reliability after v0.3.5 proved that local Firefox authentication and Instagram media downloads can work, but a sequence of publications failed with `JSONDecodeError`.

## Request pacing
- Queue entries remain sequential.
- A configurable inter-publication delay defaults to 12 seconds; UI allows 5–60 seconds.
- gallery-dl continues to use `--sleep-request 6-12`, `--retries 2`, and `--http-timeout 30`.
- The app does not parallelize Instagram publication downloads.

## Per-publication retry policy
- At most one application-level retry per publication.
- Retryable categories: HTTP 429, unexpected response/JSON decode errors, HTTP 5xx, network errors, and timeouts.
- Authentication/challenge errors (401/403, login-required, checkpoint/challenge) are not blindly retried.
- Backoff starts at 20 seconds and is capped at 300 seconds.
- For rate limits, use `Retry-After` when exposed by gallery-dl logs; otherwise wait 60 seconds. If the requested wait exceeds the configured 300-second in-process ceiling, do not retry early; leave it for a later manual retry.
- The retry cleans the publication's temporary staging directory before making the second attempt, avoiding stale partial output.
- A successful retry is recorded as a recovered download with both attempt records.

## Instagram circuit breaker
- HTTP 429 opens the circuit if the single retry still fails.
- Repeated auth/challenge, unexpected-response, or server errors open the circuit after two consecutive failed publications.
- Once open, remaining Instagram queue entries become `deferred` and do not issue more Instagram requests.
- Direct media URLs continue processing.
- A successful Instagram download resets the consecutive-failure counter.
- This is deliberately conservative: a JSON decoding error can be a temporary API response issue, an HTML challenge, or another unexpected payload. The app records the available HTTP metadata but does not assume which one occurred.

## Diagnostics
- gallery-dl runs with verbose output.
- Each attempt records elapsed time, return code, last relevant HTTP status, content type, content length, error category, retryability, Retry-After, and whether gallery-dl logged successful Firefox cookie extraction.
- Only sanitized log excerpts are stored. Cookie values and Cookie/Authorization headers are redacted.
- The actual response body is not captured or persisted.
- If gallery-dl does not expose HTTP headers/status in its logs, the relevant fields remain null instead of being guessed.

## Recovery UX
- Queue status includes `Failed` and `Deferred`.
- `Retry failed / deferred` filters the current queue to only those unsuccessful entries.
- The user must confirm the retry and is warned to wait after a rate limit.
- Successful entries are excluded from retry-only runs.
- Retry jobs receive a fresh package/job ID.

## Reporting
- `assets_downloaded` counts successful queue entries.
- `files_saved` counts individual media files.
- `assets_failed` counts actual failed attempts after retry policy.
- `assets_deferred` counts Instagram items intentionally not attempted after the circuit opened.
- CSV, JSON, Markdown report, completion dialog and history use unambiguous asset/file terminology.

## Validation scope
Automated tests cover classification, Retry-After parsing, one-time backoff retry, non-retry of authentication errors, circuit-breaker deferral, and report counts. These tests do not establish that Instagram's current live API will behave identically on the user's Windows machine.
