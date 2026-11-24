# GTL AI Asset Downloader v1.1.0 — Profile Thumbnail Preview Design

## Goal
Let a user visually identify specific Instagram publications before adding them to the download queue, without downloading the original post media.

## Data extraction
- `_thumbnail_url_from_record()` checks explicit thumbnail fields first (`post_thumbnail`, `thumbnail_url`, `thumbnail_src`, `thumbnail`, `preview_url`, `preview`, cover fields; `display_url` is only a final fallback after thumbnail-sized candidates).
- It supports nested `thumbnail_resources`, `display_resources`, `image_versions2.candidates` and `image_versions.candidates`.
- For candidate arrays, it prefers the largest candidate no wider than 640px; if unavailable, it chooses the smallest known candidate.
- It deliberately does not fall back to gallery-dl's generic `url` field because that may be the full-resolution original media.
- The normalized publication exposes a transient `preview_url` to the UI.

## Thumbnail fetching
- Runs in a bounded pool of four background workers.
- Uses HTTPX streaming with a 12-second timeout.
- Only HTTPS URLs on `instagram.com`, `cdninstagram.com` and `fbcdn.net` hosts are accepted.
- Redirect destinations are checked again.
- Only `image/*` responses are accepted.
- Content-Length and streamed bytes are limited to 4 MiB.
- Pillow verifies the image, converts it to RGB and resizes it to fit within 640x640.
- Cache is keyed by a SHA-256 digest of shortcode and stored in the user's app-data directory.
- A valid cached image is reused without a network request.

## UI
- Profile Extractor Treeview adds a 78px-high image column and compact 64px thumbnails.
- Generic image and Reel placeholders are used while previews load or when no URL is exposed.
- Thumbnail requests run off the UI thread; Tk image objects and widgets are updated only on the Tk main thread.
- Clicking the thumbnail opens a larger preview window.
- Double-clicking any row opens the same preview.
- The preview includes publication type, shortcode, date, media count, caption and an Open on Instagram action.
- A generation token prevents results from an earlier scan updating a newer table.

## Data hygiene
- Signed/expiring preview URLs are kept only in transient `profile_results` UI data.
- `profile_discovery_data` is copied without `preview_url`, so the signed URL is not included in exported ZIP metadata.
- Selected queue assets receive only the existing stable publication metadata; preview URLs are not copied into the download manifest.

## Limitations
- Instagram/gallery-dl may not expose a thumbnail URL for every post.
- Placeholder icons are expected when the metadata has no supported thumbnail URL or the CDN refuses the request.
- The feature does not use browser cookies to fetch thumbnails and does not bypass login walls or access controls.
- It does not fetch all carousel images; the preview represents the publication's available cover/first preview only.

## Test coverage
- Direct and nested thumbnail field extraction.
- Resolution candidate selection.
- No fallback to full media `url`.
- HTTPS/host allowlist.
- Image transfer, validation, resize and cache reuse.
- Rejection of untrusted hosts.
