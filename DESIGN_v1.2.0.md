# GTL AI Asset Downloader v1.2.0 — UX Workflow Design

## Persistent download action
The primary Download Assets button lives in the top action row next to source-mode navigation. This keeps it visible even when Profile Extractor's content is selected. The button is disabled while a job is running. If the user navigates away and returns during a job, the recreated button reflects the active state.

## Profile scanning and preview progress
- A profile scan starts an indeterminate progress bar.
- The existing discovery callback changes the stage label between posts and Reels.
- After metadata discovery, the bar switches to determinate mode for thumbnail processing.
- Every thumbnail task increments progress, even if the CDN image is unavailable.
- The summary reports both discovered publication count and previews successfully loaded.
- The profile result viewport is fixed-height and shows three rows at a time, with scrolling for the rest, protecting the queue and primary action area from vertical overflow.

## Download progress
The existing per-asset progress bar and status callback remain in the footer. The queue is refreshed after each completed successful asset, so the user sees it shrink during a job.

## Output directory resolution
`application_dir()` resolves to:
- `Path(sys.executable).resolve().parent` in a frozen/PyInstaller executable.
- `Path(__file__).resolve().parent` in source mode.

The default output is `<application_dir>/output`. The previous app default `~/Documents/GTL AI Assets` is treated as legacy default and migrated. Non-default existing paths are preserved as user customizations. The settings file persists `output_dir_user_set` for future versions.

## Queue cleanup semantics
Only results with status `downloaded` or `duplicate` are removed. `failed`, `deferred`, and any queued items added after the job began remain. The cleanup helper is pure and used incrementally during progress callbacks and again at job completion. In-flight asset IDs remain reserved to avoid collisions after successful entries are removed.

## Validation and limitations
- Automated tests cover queue pruning and output directory resolution.
- No Tk GUI is available in the build environment; visual layout must be checked on Windows at normal (non-fullscreen) size.
- The PyInstaller EXE must be rebuilt from this source release.
