# GTL AI Asset Downloader — v1.2.0

## Download action visibility
- Moved **Download Assets** to the persistent top action bar alongside the source-mode tabs.
- It remains visible while Profile Extractor is selected and no longer depends on the height of the profile results or queue panels.
- The button disables while a download job is active, including after navigating away and back to the Downloader.

## Progress feedback
- Profile discovery uses an indeterminate progress indicator while querying posts and Reels metadata.
- Status text identifies the active discovery stage (posts or Reels).
- Once discovery completes, thumbnail fetching uses a determinate progress bar.
- Thumbnail progress advances for every publication processed, including unavailable/failed thumbnail previews.
- Existing per-asset download progress and status messages remain in place.

## Output location
- New default: `output` beside the running executable.
- When running from source, the default is `output` beside `main.py`.
- Existing custom output directories are preserved.
- The old default `Documents/GTL AI Assets` is migrated to the new application-relative output folder.
- The selected custom path is persisted when chosen through Browse or edited in the output entry.

## Queue cleanup
- Assets with status `downloaded` are removed from the visible queue immediately after successful completion.
- Assets with status `duplicate` are also removed because they are already represented by previously acquired content.
- Assets with status `failed` or `deferred` remain in the queue and retain their retry status.
- A final cleanup pass runs when the job finishes as a safety net.
- Assets added while a job is running are not removed by that job's results.
- Asset IDs currently in flight are reserved to prevent accidental ID reuse while the queue is shrinking.

## Validation
- Python syntax compilation: passed.
- Desktop entry-point import: passed.
- Unit tests: 50/50 passed.
- Queue cleanup helper: tested for downloaded, duplicate, failed, deferred and newly-added queue items.
- Output path resolution: tested for frozen executable and source mode.
- Live GUI layout and Windows EXE build: pending verification on the user's Windows machine.
