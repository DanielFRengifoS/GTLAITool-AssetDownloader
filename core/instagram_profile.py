"""Instagram public-profile discovery using gallery-dl metadata mode.

Discovery is intentionally separate from downloading. It enumerates public posts
and reels, normalizes gallery-dl JSON records, merges both streams, de-duplicates
shortcodes, and returns at most the requested number of publications.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urljoin

import httpx
from PIL import Image, UnidentifiedImageError

from .engine import AssetError, validate_instagram_profile_url
from .firefox_profiles import browser_cookie_selector, validate_auth_config, redact_sensitive_text

DEFAULT_LIMIT = 50
MAX_LIMIT = 500


MIN_GALLERY_DL_VERSION = (1, 32, 21)
GALLERY_DL_SOURCE = "Codeberg master branch (official upstream archive)"
THUMBNAIL_MAX_BYTES = 4 * 1024 * 1024
THUMBNAIL_MAX_EDGE = 640
THUMBNAIL_MAX_PIXELS = 40_000_000
THUMBNAIL_HOST_SUFFIXES = ("cdninstagram.com", "fbcdn.net", "instagram.com")


def _redact_sensitive(text: str) -> str:
    """Compatibility wrapper for the shared browser/session secret scrubber."""
    return redact_sensitive_text(text)


def _gallery_dl_version(timeout: int = 20) -> str:
    """Return the exact gallery-dl version bundled with this app."""
    try:
        completed = subprocess.run(
            _gallery_dl_command() + ["--version"],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace"
        )
    except Exception as exc:
        return f"unknown (version check failed: {type(exc).__name__}: {exc})"
    output = (completed.stdout or completed.stderr or "").strip()
    return output or f"unknown (exit={completed.returncode})"


def _version_tuple(value: str):
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", value or "")
    return tuple(map(int, match.groups())) if match else None


def _gallery_dl_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--gallery-dl-helper"]
    return [sys.executable, "-m", "gallery_dl"]


def _walk_dicts(value):
    """Yield metadata dictionaries from gallery-dl JSON shapes."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, (dict, list, tuple)):
                yield from _walk_dicts(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            if isinstance(child, (dict, list, tuple)):
                yield from _walk_dicts(child)


def _first(data: dict, keys, default=None):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def _parse_gallery_json(stdout: str) -> list[dict]:
    """Parse gallery-dl JSON output as a document or concatenated JSON values."""
    records = []

    def collect(payload):
        for item in _walk_dicts(payload):
            shortcode = _first(item, ("post_shortcode", "shortcode", "code"))
            if not shortcode:
                continue
            shortcode = str(shortcode)
            if re.fullmatch(r"[A-Za-z0-9_-]+", shortcode):
                records.append(item)

    stripped = (stdout or "").strip()
    if not stripped:
        return records

    try:
        collect(json.loads(stripped))
        return records
    except json.JSONDecodeError:
        pass

    # Some gallery-dl jobs emit multiple pretty-printed JSON values back-to-back.
    decoder = json.JSONDecoder()
    position = 0
    decoded_any = False
    while position < len(stripped):
        while position < len(stripped) and stripped[position].isspace():
            position += 1
        if position >= len(stripped):
            break
        try:
            payload, next_position = decoder.raw_decode(stripped, position)
        except json.JSONDecodeError:
            decoded_any = False
            break
        collect(payload)
        decoded_any = True
        position = next_position
    if decoded_any and position >= len(stripped):
        return records

    # Last fallback: one JSON value per line.
    records.clear()
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            collect(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _safe_thumbnail_url(value) -> str | None:
    """Accept only HTTPS media URLs hosted by Instagram's known image CDNs."""
    if isinstance(value, dict):
        value = _first(value, ("url", "src", "uri", "display_url"))
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            return None
        if parsed.port not in (None, 443):
            return None
        if not any(host == suffix or host.endswith("." + suffix) for suffix in THUMBNAIL_HOST_SUFFIXES):
            return None
        return value
    except Exception:
        return None


def _choose_image_candidate(value) -> str | None:
    """Choose a reasonably sized image candidate from Instagram version arrays."""
    if isinstance(value, dict):
        candidates = value.get("candidates")
        if isinstance(candidates, list):
            value = candidates
        else:
            return _safe_thumbnail_url(value)
    if not isinstance(value, (list, tuple)):
        return _safe_thumbnail_url(value)

    usable = []
    for candidate in value:
        if not isinstance(candidate, dict):
            continue
        url = _safe_thumbnail_url(candidate)
        if not url:
            continue
        try:
            width = int(candidate.get("width") or candidate.get("config_width") or 0)
        except (TypeError, ValueError):
            width = 0
        usable.append((width, url))
    if not usable:
        return None

    # Prefer the largest candidate no wider than our 640px preview; if none
    # exists, choose the smallest available candidate rather than full-size media.
    under_limit = [entry for entry in usable if 0 < entry[0] <= THUMBNAIL_MAX_EDGE]
    if under_limit:
        return max(under_limit, key=lambda entry: entry[0])[1]
    known_sizes = [entry for entry in usable if entry[0] > 0]
    if known_sizes:
        return min(known_sizes, key=lambda entry: entry[0])[1]
    return usable[0][1]


def _thumbnail_url_from_record(raw: dict) -> str | None:
    """Find thumbnail-only metadata; deliberately never falls back to media `url`."""
    direct_keys = (
        "post_thumbnail", "thumbnail_url", "thumbnail_src", "thumbnail",
        "preview_url", "preview", "cover_url", "cover_photo_url",
    )
    for key in direct_keys:
        if key not in raw:
            continue
        candidate = _safe_thumbnail_url(raw.get(key))
        if candidate:
            return candidate

    for key in ("thumbnail_resources", "display_resources", "image_versions2", "image_versions"):
        if key in raw:
            candidate = _choose_image_candidate(raw.get(key))
            if candidate:
                return candidate

    # Some gallery-dl metadata nests post-level fields under a media/post object.
    for nested_key in ("post", "media", "image", "carousel_media"):
        nested = raw.get(nested_key)
        if isinstance(nested, dict):
            candidate = _thumbnail_url_from_record(nested)
            if candidate:
                return candidate
        elif isinstance(nested, list):
            for child in nested:
                if isinstance(child, dict):
                    candidate = _thumbnail_url_from_record(child)
                    if candidate:
                        return candidate

    # display_url is a last-resort cover image. Prefer thumbnail-sized variants
    # above whenever the extractor supplies them.
    return _safe_thumbnail_url(raw.get("display_url"))


def _thumbnail_cache_path(cache_dir: Path, shortcode: str) -> Path:
    token = hashlib.sha256(str(shortcode).encode("utf-8")).hexdigest()[:24]
    return Path(cache_dir) / f"{token}.jpg"


def fetch_profile_thumbnail(url: str | None, cache_dir: Path,
                             shortcode: str, timeout: float = 12.0) -> Path | None:
    """Fetch one small, validated Instagram thumbnail into a local persistent cache."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = _thumbnail_cache_path(cache_dir, shortcode)
    if cache_path.is_file():
        try:
            with Image.open(cache_path) as cached:
                cached.verify()
            return cache_path
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            cache_path.unlink(missing_ok=True)

    safe_url = _safe_thumbnail_url(url)
    if not safe_url:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": "https://www.instagram.com/",
    }
    data = bytearray()
    temp_path = None
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
            # Check every hop before sending the request, including intermediate
            # redirects. Checking response.url after automatic redirects is too late.
            for redirect_count in range(6):
                with client.stream("GET", safe_url) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location", "")
                        safe_url = _safe_thumbnail_url(urljoin(safe_url, location)) if location else None
                        if not safe_url or redirect_count == 5:
                            return None
                        continue
                    response.raise_for_status()
                    final_url = _safe_thumbnail_url(str(response.url))
                    if not final_url:
                        return None
                    content_type = str(response.headers.get("content-type", "")).lower()
                    if not content_type.startswith("image/"):
                        return None
                    length = response.headers.get("content-length")
                    if length:
                        try:
                            if int(length) > THUMBNAIL_MAX_BYTES:
                                return None
                        except ValueError:
                            pass
                    for chunk in response.iter_bytes(64 * 1024):
                        data.extend(chunk)
                        if len(data) > THUMBNAIL_MAX_BYTES:
                            return None
                    break

        if not data:
            return None
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > THUMBNAIL_MAX_PIXELS:
                return None
            source.verify()
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > THUMBNAIL_MAX_PIXELS:
                return None
            image = source.convert("RGB")
            image.thumbnail((THUMBNAIL_MAX_EDGE, THUMBNAIL_MAX_EDGE), Image.Resampling.LANCZOS)
            with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".tmp.jpg", delete=False) as temporary:
                temp_path = Path(temporary.name)
                image.save(temporary, format="JPEG", quality=86, optimize=True)
            temp_path.replace(cache_path)
        return cache_path
    except (httpx.HTTPError, OSError, UnidentifiedImageError, Image.DecompressionBombError, ValueError):
        return None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _to_timestamp(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # gallery-dl commonly exposes Unix seconds.
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return None
    return None


def _normalize_records(records: list[dict], username: str, category: str) -> list[dict]:
    grouped = {}
    for raw in records:
        shortcode = str(_first(raw, ("post_shortcode", "shortcode", "code"), ""))
        if not shortcode:
            continue

        # Prefer explicit post-level values; gallery-dl may emit one record per
        # media file, so accumulate counts without duplicating publications.
        timestamp = _to_timestamp(_first(
            raw, ("post_date", "date_timestamp", "timestamp", "date")
        ))
        date_text = _first(raw, ("post_date", "date", "date_string"))
        if isinstance(date_text, (int, float)):
            date_text = None

        typename = str(_first(raw, ("typename", "media_type", "type"), "")).lower()
        subcategory = str(_first(raw, ("subcategory",), category)).lower()
        publication_type = "reel" if category == "reels" or "video" in typename else "post"
        post_url = f"https://www.instagram.com/{'reel' if publication_type == 'reel' else 'p'}/{shortcode}/"

        item = grouped.setdefault(shortcode, {
            "shortcode": shortcode,
            "publication_type": publication_type,
            "post_url": post_url,
            "profile_url": f"https://www.instagram.com/{username}/",
            "date": str(date_text) if date_text else None,
            "timestamp": timestamp,
            "caption": _first(raw, ("post_caption", "caption", "description"), ""),
            "preview_url": _thumbnail_url_from_record(raw),
            "is_pinned": bool(_first(raw, ("pinned", "is_pinned"), False)),
            "media_count": 0,
            "source_category": category,
            "media_ids": set(),
        })

        if item["timestamp"] is None and timestamp is not None:
            item["timestamp"] = timestamp
        if not item["date"] and date_text:
            item["date"] = str(date_text)
        if not item["caption"]:
            item["caption"] = _first(raw, ("post_caption", "caption", "description"), "")
        if not item.get("preview_url"):
            item["preview_url"] = _thumbnail_url_from_record(raw)
        media_id = _first(raw, ("media_id", "id"))
        if media_id is not None:
            item["media_ids"].add(str(media_id))
        count = _first(raw, ("post_media_count", "media_count", "count"), None)
        if isinstance(count, int) and count > item["media_count"]:
            item["media_count"] = count

    normalized = []
    for item in grouped.values():
        # When per-file metadata is emitted, the distinct media IDs provide a
        # conservative carousel count. Do not claim a count when unknown.
        if item["media_count"] == 0 and item["media_ids"]:
            item["media_count"] = len(item["media_ids"])
        item["media_ids"] = sorted(item["media_ids"])
        normalized.append(item)

    # Newest first. Entries without timestamps remain after dated entries.
    normalized.sort(key=lambda x: (
        x["timestamp"] is not None,
        x["timestamp"] if x["timestamp"] is not None else 0
    ), reverse=True)
    return normalized


def _run_discovery(profile_url: str, category: str, limit: int, timeout: int, username: str | None = None, auth_config: dict | None = None) -> tuple[list[dict], str]:
    username = username or [part for part in urlparse(profile_url).path.split("/") if part][0]
    command = _gallery_dl_command() + [
        "--config-ignore",
        "--no-input",
        "--no-colors",
        "--verbose",
        "--http-timeout", "30",
        "--retries", "1",
        "--sleep-request", "6-12",
        "--resolve-json",
        "-o", f"extractor.instagram.include={category}",
        "-o", f"extractor.instagram.max-posts={limit}",
    ]
    cookie_selector = browser_cookie_selector(auth_config)
    if cookie_selector:
        command.extend(["--cookies-from-browser", cookie_selector])
    command.append(profile_url)
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace"
        )
    except subprocess.TimeoutExpired as exc:
        raise AssetError(
            f"Instagram {category} discovery timed out after {timeout} seconds. "
            "No media was downloaded; retry later."
        ) from exc
    except OSError as exc:
        raise AssetError(f"Could not start gallery-dl: {exc}") from exc

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    records = _parse_gallery_json(stdout)
    normalized = _normalize_records(records, username, category)

    # Keep a compact diagnostic trail. This makes a zero-result scan explain
    # whether the extractor returned no output, emitted unrecognized JSON, or
    # reported a login/rate-limit/HTTP error.
    diagnostic_parts = [
        f"{category}: exit={completed.returncode}, "
        f"stdout_bytes={len(stdout.encode('utf-8', errors='replace'))}, "
        f"json_records={len(records)}, publications={len(normalized)}"
    ]
    if stderr.strip():
        diagnostic_parts.append("stderr:\n" + _redact_sensitive(stderr.strip()[-1800:]))
    if stdout.strip() and not normalized:
        # Report JSON structure/keys rather than raw metadata, which can contain
        # captions or short-lived signed media URLs.
        first_line = next((line.strip() for line in stdout.splitlines() if line.strip()), "")
        try:
            sample_payload = json.loads(first_line)
            sample_dicts = list(_walk_dicts(sample_payload))
            if sample_dicts:
                keys = sorted({key for item in sample_dicts[:5] for key in item.keys()})
                diagnostic_parts.append("stdout JSON metadata keys: " + ", ".join(keys[:80]))
            else:
                diagnostic_parts.append("stdout JSON top-level type: " + type(sample_payload).__name__)
        except json.JSONDecodeError:
            redacted = re.sub(r"https?://\S+", "<URL>", stdout.strip()[:500])
            diagnostic_parts.append("stdout non-JSON sample: " + redacted)

    diagnostic = "\n".join(diagnostic_parts)
    if completed.returncode != 0 and not normalized:
        raise AssetError(f"Instagram {category} discovery failed.\n{diagnostic}")

    return normalized[:limit], diagnostic


def discover_profile(profile_url: str, limit: int = DEFAULT_LIMIT,
                     timeout: int = 180, progress_callback=None,
                     auth_config: dict | None = None) -> dict:
    """Discover posts and Reels from the exact requested username URL."""
    profile_url = validate_instagram_profile_url(profile_url)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise AssetError(f"Publication limit must be between 1 and {MAX_LIMIT}.")
    username = [part for part in urlparse(profile_url).path.split("/") if part][0]
    try:
        auth = validate_auth_config(auth_config)
    except ValueError as exc:
        raise AssetError(str(exc)) from exc

    gallery_dl_version = _gallery_dl_version()
    parsed_version = _version_tuple(gallery_dl_version)
    if parsed_version is None or parsed_version < MIN_GALLERY_DL_VERSION:
        raise AssetError(
            f"This build is using gallery-dl {gallery_dl_version}, which is older than "
            "the minimum supported baseline 1.32.21. Rebuild using the current source package."
        )

    auth_label = "Firefox browser session" if auth["mode"] == "firefox" else "anonymous"
    diagnostics = [
        f"gallery-dl version: {gallery_dl_version}",
        f"gallery-dl source: {GALLERY_DL_SOURCE}",
        f"Authentication source: {auth_label}",
        f"Requested profile URL: {profile_url}",
    ]

    streams = []
    for category in ("posts", "reels"):
        if progress_callback:
            progress_callback(
                f"Discovering {category} for @{username} using {auth_label}…"
            )
        found, diagnostic = _run_discovery(
            profile_url, category, limit, timeout,
            username=username, auth_config=auth
        )
        streams.extend(found)
        if diagnostic:
            diagnostics.append(diagnostic)

    merged = {}
    for item in streams:
        existing = merged.get(item["shortcode"])
        if existing is None:
            merged[item["shortcode"]] = item
        else:
            if existing.get("timestamp") is None and item.get("timestamp") is not None:
                existing["timestamp"] = item["timestamp"]
                existing["date"] = item.get("date")
            existing["media_count"] = max(
                existing.get("media_count", 0), item.get("media_count", 0)
            )
            if not existing.get("preview_url") and item.get("preview_url"):
                existing["preview_url"] = item["preview_url"]
            if item["publication_type"] == "reel":
                existing["publication_type"] = "reel"
                existing["post_url"] = item["post_url"]

    publications = list(merged.values())
    publications.sort(key=lambda x: (
        x.get("timestamp") is not None,
        x.get("timestamp") or 0
    ), reverse=True)
    publications = publications[:limit]

    if not publications:
        details = "\n\n".join(diagnostics) or "gallery-dl returned no diagnostic output."
        lower = details.lower()
        if "401" in lower or "unauthorized" in lower:
            diagnosis = (
                "Instagram returned HTTP 401. With Firefox authentication enabled, verify that "
                "the selected Firefox profile is the one currently signed into Instagram. "
                "This can also be an Instagram-side request rejection; it is not proof that the "
                "profile has no publications."
            )
        elif "polarisprofilepoststabcontentquery" in lower or "polarisprofilereelstabcontentquery" in lower:
            diagnosis = (
                "Instagram rejected or changed its posts/Reels GraphQL operation. A valid Firefox "
                "session may not fix this upstream API incompatibility. The extractor will not "
                "repeat requests aggressively."
            )
        elif "cookies.sqlite" in lower or "firefox profile" in lower:
            diagnosis = (
                "gallery-dl could not read the selected Firefox profile. Confirm the profile "
                "selection; if its cookie database is locked, close Firefox completely and retry."
            )
        else:
            diagnosis = (
                "The requested profile URL was used directly, but gallery-dl returned no "
                "parseable publications. This is an extraction/API failure, not a fabricated "
                "numeric profile-ID result."
            )
        raise AssetError(diagnosis + "\n\n" + details[-5000:])

    for index, item in enumerate(publications, start=1):
        item["asset_id"] = f"instagram-{index:03d}"
        item["rights_status"] = "unknown"

    return {
        "schema_version": "1.0",
        "discovery_version": "1.1.0",
        "profile_url": profile_url,
        "profile_id": None,
        "username": username,
        "scope": "own",
        "requested_limit": limit,
        "discovered_count": len(publications),
        "complete": True,
        "limit_reached": len(publications) == limit,
        "warnings": [],
        "gallery_dl_version": gallery_dl_version,
        "gallery_dl_source": GALLERY_DL_SOURCE,
        "authentication_source": auth_label,
        "diagnostics": diagnostics,
        "publications": publications,
    }

