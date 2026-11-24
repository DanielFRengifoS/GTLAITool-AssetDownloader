# GTL AI Asset Downloader — v1.1.0

## Profile Extractor: visual previews
- Added a PREVIEW column with compact image thumbnails for discovered Instagram posts and Reels.
- Clicking a thumbnail opens a larger in-app preview window.
- Double-clicking a publication row also opens the preview.
- Preview window includes account name, publication type, shortcode, date, media count, caption and an Open on Instagram action.
- Reel thumbnails receive a small play indicator.
- Generic post/Reel placeholders remain visible if Instagram does not expose a supported thumbnail URL.

## Performance
- Thumbnail fetching runs off the Tkinter UI thread using a bounded four-worker pool.
- Each thumbnail is resized locally to a maximum 640px edge.
- Thumbnails are cached locally by publication shortcode and reused on subsequent scans.
- The original post media is not downloaded to generate previews.

## Security and privacy
- Only HTTPS URLs on Instagram/CDN host suffixes are accepted.
- Redirect destinations are revalidated.
- Only image content is accepted.
- Thumbnail transfer is capped at 4 MB per item.
- Signed/expiring preview URLs remain transient in memory and are excluded from profile discovery metadata saved into the ZIP.
- No cookies are exported or sent to a GTL AI service.

## Versioning
- v0.3.6 is the previously verified release designated by the user as v1.0.
- This visual preview feature is the next minor release: v1.1.0.

## Validation
- Python syntax compilation: passed.
- Automated tests: 42/42 passed.
- Thumbnail URL allowlist, candidate selection, no-full-media fallback, resizing, cache reuse and untrusted-host rejection: covered.
- Live Instagram thumbnail availability: pending verification on the user's Windows installation.
- Native Windows EXE build: pending on Windows.
