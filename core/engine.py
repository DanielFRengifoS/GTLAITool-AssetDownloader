#!/usr/bin/env python3
"""GTL AI Asset Downloader — Instagram publication + direct media URL MVP."""

from __future__ import annotations

import csv
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from .firefox_profiles import browser_cookie_selector, validate_auth_config, redact_sensitive_text


VERSION = "1.3.0"
ASSET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
INSTAGRAM_PATH_TYPES = {"p": "post", "reel": "reel", "tv": "tv"}
REDIRECT_CODES = {301, 302, 303, 307, 308}
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
MAX_REDIRECTS = 5
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".3gp"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif"}


class AssetError(Exception):
    """An expected, reportable asset-level error."""


class InstagramExtractionError(AssetError):
    """Instagram extraction error with structured, sanitized diagnostics."""

    def __init__(self, message: str, diagnostic: dict | None = None):
        super().__init__(message)
        self.diagnostic = diagnostic or {}


def _parse_retry_after_seconds(text: str) -> int | None:
    """Extract Retry-After from gallery-dl diagnostics (seconds or HTTP date)."""
    match = re.search(r"(?im)retry-after\s*:\s*([^\r\n]+)", text or "")
    if not match:
        return None
    value = match.group(1).strip()
    if value.isdigit():
        return max(0, int(value))
    try:
        from email.utils import parsedate_to_datetime
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int((parsed - datetime.now(timezone.utc)).total_seconds()))
    except Exception:
        return None


def _gallery_failure_diagnostic(stdout: str, stderr: str, returncode: int | None,
                                duration_seconds: float, timed_out: bool = False) -> dict:
    """Classify extractor output without retaining raw credentials or bodies."""
    raw = (stderr or "") + "\n" + (stdout or "")
    safe = redact_sensitive_text(raw)
    lower = safe.lower()

    status_matches = re.findall(
        r"(?:HTTP/\d(?:\.\d)?\s+(\d{3})|\bstatus(?:_code)?\s*[:= ]\s*(\d{3}))",
        safe,
        flags=re.IGNORECASE,
    )
    statuses = []
    for pair in status_matches:
        for value in pair:
            if value:
                statuses.append(int(value))
                break
    http_status = next((code for code in reversed(statuses) if code >= 400), statuses[-1] if statuses else None)

    content_type_match = re.findall(r"(?im)content-type\s*:\s*([^\r\n]+)", safe)
    content_length_match = re.findall(r"(?im)content-length\s*:\s*(\d+)", safe)
    retry_after = _parse_retry_after_seconds(safe)

    if timed_out or "timed out" in lower or "timeout" in lower:
        category = "network_timeout"
    elif http_status == 429 or "too many requests" in lower or "rate limit" in lower:
        category = "rate_limited"
    elif http_status in {401, 403} or any(token in lower for token in (
        "login_required", "login required", "checkpoint_required", "challenge_required",
        "feedback_required", "checkpoint", "challenge"
    )):
        category = "auth_or_challenge"
    elif "jsondecodeerror" in lower or "expecting value: line 1 column 1" in lower:
        category = "unexpected_response"
    elif http_status is not None and 500 <= http_status <= 599:
        category = "server_error"
    elif "connectionerror" in lower or "connection reset" in lower or "temporarily unavailable" in lower:
        category = "network_error"
    else:
        category = "extraction_error"

    retryable = category in {
        "rate_limited", "unexpected_response", "server_error",
        "network_error", "network_timeout",
    }
    excerpt = re.sub(r"\s+", " ", safe).strip()[-2400:]
    return {
        "http_status": http_status,
        "content_type": content_type_match[-1].strip() if content_type_match else None,
        "response_bytes": int(content_length_match[-1]) if content_length_match else None,
        "error_category": category,
        "retryable": retryable,
        "retry_after_seconds": retry_after,
        "return_code": returncode,
        "duration_seconds": round(max(0.0, duration_seconds), 3),
        "browser_cookies_loaded": bool(re.search(r"extracted\s+\d+\s+cookies\s+from\s+firefox", lower)),
        "diagnostic_excerpt": excerpt or "No diagnostic output was returned.",
    }


def _clean_staging_target(asset: dict, staging_dir: Path) -> None:
    target_dir = staging_dir / f"instagram_{safe_token(asset['asset_id'])}"
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_token(value: str, fallback: str = "asset") -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-._")
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        value = "asset-" + value
    return value[:80] or fallback


def validate_http_url(url: str, *, resolve_public_host: bool = False) -> str:
    if not isinstance(url, str) or not url.strip():
        raise AssetError("URL is missing.")
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise AssetError("URLs containing control characters are not accepted.")
    try:
        parsed = urlparse(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise AssetError("URL contains an invalid hostname or port.") from exc
    if parsed.scheme not in {"http", "https"}:
        raise AssetError("Only http:// and https:// URLs are supported.")
    if not parsed.hostname:
        raise AssetError("URL has no hostname.")
    if parsed.username or parsed.password:
        raise AssetError("URLs containing embedded credentials are not accepted.")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost"} or host.endswith(".localhost") or host.endswith(".local"):
        raise AssetError("Local and private network destinations are blocked.")
    if port not in {None, 80, 443}:
        raise AssetError("Non-standard URL ports are not accepted.")
    if resolve_public_host:
        try:
            try:
                addresses = [ipaddress.ip_address(host)]
            except ValueError:
                records = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                             type=socket.SOCK_STREAM)
                addresses = list({ipaddress.ip_address(record[4][0]) for record in records})
            if not addresses or any(not address.is_global for address in addresses):
                raise AssetError("URL resolves to a non-public network address.")
        except socket.gaierror as exc:
            raise AssetError(f"Could not resolve URL hostname: {host}") from exc
    return url.strip()


def validate_instagram_profile_url(url: str) -> str:
    url = validate_http_url(url)
    parsed = urlparse(url)
    if parsed.hostname.lower().rstrip(".") not in INSTAGRAM_HOSTS:
        raise AssetError("profile_url must point to instagram.com.")
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) != 1 or segments[0].lower() in {"p", "reel", "tv", "stories", "explore"}:
        raise AssetError("profile_url must contain one Instagram username, not a publication or discovery URL.")
    return url


def resolve_instagram_post_url(asset: dict) -> str:
    post_url = asset.get("post_url")
    if post_url:
        post_url = validate_http_url(post_url)
        parsed = urlparse(post_url)
        if parsed.hostname.lower().rstrip(".") not in INSTAGRAM_HOSTS:
            raise AssetError("post_url must point to instagram.com.")
        segments = [segment for segment in parsed.path.split("/") if segment]
        if len(segments) != 2 or segments[0] not in INSTAGRAM_PATH_TYPES:
            raise AssetError("Instagram input must be a specific /p/, /reel/, or /tv/ publication URL.")
        if not SHORTCODE_RE.fullmatch(segments[1]):
            raise AssetError("Instagram publication shortcode is invalid.")
        return post_url

    profile_url = asset.get("profile_url")
    shortcode = asset.get("shortcode")
    if not profile_url or not shortcode:
        raise AssetError("Instagram assets require post_url, or both profile_url and shortcode.")
    validate_instagram_profile_url(profile_url)
    if not SHORTCODE_RE.fullmatch(str(shortcode)):
        raise AssetError("Instagram shortcode is invalid.")
    publication_type = asset.get("publication_type", "post")
    if not isinstance(publication_type, str):
        raise AssetError("publication_type must be post, reel, or tv.")
    path_type = {"post": "p", "reel": "reel", "tv": "tv"}.get(publication_type)
    if not path_type:
        raise AssetError("publication_type must be post, reel, or tv.")
    return f"https://www.instagram.com/{path_type}/{shortcode}/"


def validate_manifest(data: dict) -> list[dict]:
    if not isinstance(data, dict):
        raise AssetError("Manifest root must be a JSON object.")
    if data.get("schema_version") != "1.0":
        raise AssetError('schema_version must be "1.0".')
    assets = data.get("assets")
    if not isinstance(assets, list) or not assets:
        raise AssetError("Manifest must contain a non-empty assets array.")

    seen = set()
    normalized = []
    for position, raw in enumerate(assets, start=1):
        if not isinstance(raw, dict):
            raise AssetError(f"Asset #{position} must be a JSON object.")
        asset = dict(raw)
        asset_id = asset.get("asset_id")
        if not isinstance(asset_id, str) or not ASSET_ID_RE.fullmatch(asset_id):
            raise AssetError(f"Asset #{position} has an invalid asset_id.")
        if asset_id in seen:
            raise AssetError(f"Duplicate asset_id: {asset_id}")
        seen.add(asset_id)

        source_type = asset.get("source_type")
        if not isinstance(source_type, str) or source_type not in {"instagram", "direct_url"}:
            raise AssetError(f"{asset_id}: source_type must be instagram or direct_url.")
        media_type = asset.get("media_type", "auto")
        if not isinstance(media_type, str) or media_type not in {"auto", "image", "video"}:
            raise AssetError(f"{asset_id}: media_type must be auto, image, or video.")
        asset["media_type"] = media_type

        if source_type == "instagram":
            asset["resolved_post_url"] = resolve_instagram_post_url(asset)
            if asset.get("media_index") is not None:
                index = asset["media_index"]
                if isinstance(index, bool) or not isinstance(index, int) or index < 1:
                    raise AssetError(f"{asset_id}: media_index must be a 1-based integer.")
            if asset.get("profile_url"):
                validate_instagram_profile_url(asset["profile_url"])
        else:
            asset["media_url"] = validate_http_url(asset.get("media_url", ""))
            if asset.get("referer_url"):
                asset["referer_url"] = validate_http_url(asset["referer_url"])
            if asset.get("source_page_url"):
                asset["source_page_url"] = validate_http_url(asset["source_page_url"])
        normalized.append(asset)
    return normalized


def load_manifest(path: Path) -> tuple[dict, list[dict]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise AssetError(f"Manifest file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AssetError(f"Manifest JSON is invalid: {exc}") from exc
    return data, validate_manifest(data)


def sniff_image(path: Path) -> str | None:
    try:
        with Image.open(path) as image:
            image.verify()
            fmt = (image.format or "").upper()
            mapping = {
                "JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp",
                "BMP": ".bmp", "TIFF": ".tiff"
            }
            if fmt in mapping:
                return mapping[fmt]
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        pass

    # AVIF is accepted by signature even if the local Pillow build lacks AVIF support.
    try:
        with path.open("rb") as handle:
            head = handle.read(64)
        if len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in {b"avif", b"avis"}:
            return ".avif"
    except OSError:
        pass
    return None


def sniff_video(path: Path, content_type: str = "") -> str | None:
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    type_map = {
        "video/mp4": ".mp4", "video/x-m4v": ".m4v", "video/quicktime": ".mov",
        "video/webm": ".webm", "video/x-matroska": ".mkv",
        "video/x-msvideo": ".avi", "video/3gpp": ".3gp"
    }
    try:
        with path.open("rb") as handle:
            head = handle.read(64)
    except OSError:
        return None
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return type_map.get(ctype, ".mp4")
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return type_map.get(ctype, ".webm")
    if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
        return ".avi"
    return None


def classify_media(path: Path, declared_type: str = "auto", content_type: str = "") -> tuple[str, str]:
    image_ext = sniff_image(path)
    video_ext = sniff_video(path, content_type)
    detected = "image" if image_ext else "video" if video_ext else None
    if not detected:
        raise AssetError("Downloaded content is not a supported raster image or recognized video file.")
    if declared_type != "auto" and declared_type != detected:
        raise AssetError(f"Expected {declared_type}, but downloaded content is {detected}.")
    return detected, image_ext or video_ext


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_direct_url(asset: dict, staging_dir: Path, max_bytes: int) -> tuple[Path, str]:
    current_url = validate_http_url(asset["media_url"], resolve_public_host=True)
    headers = {
        "User-Agent": f"GTL-AI-Asset-Downloader/{VERSION}",
        "Accept": "image/avif,image/webp,image/apng,image/*,video/*;q=0.9,*/*;q=0.5",
    }
    referer = asset.get("referer_url") or asset.get("source_page_url")
    if referer:
        headers["Referer"] = validate_http_url(referer)

    timeout = httpx.Timeout(35.0, connect=12.0)
    temp_path = staging_dir / f"{safe_token(asset['asset_id'])}.download"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
            for redirect_count in range(MAX_REDIRECTS + 1):
                current_url = validate_http_url(current_url, resolve_public_host=True)
                with client.stream("GET", current_url) as response:
                    if response.status_code in REDIRECT_CODES:
                        location = response.headers.get("location")
                        if not location:
                            raise AssetError("Server returned a redirect without a Location header.")
                        if redirect_count >= MAX_REDIRECTS:
                            raise AssetError("Too many redirects.")
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code >= 400:
                        raise AssetError(f"Media server returned HTTP {response.status_code}.")
                    declared_length = response.headers.get("content-length")
                    if declared_length:
                        try:
                            if int(declared_length) > max_bytes:
                                raise AssetError(f"File exceeds the configured {max_bytes // (1024 * 1024)} MB limit.")
                        except ValueError:
                            pass
                    total = 0
                    with temp_path.open("wb") as output:
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                raise AssetError(f"File exceeds the configured {max_bytes // (1024 * 1024)} MB limit.")
                            output.write(chunk)
                    return temp_path, response.headers.get("content-type", "")
        raise AssetError("Unable to retrieve media URL.")
    except httpx.TimeoutException as exc:
        temp_path.unlink(missing_ok=True)
        raise AssetError("Media request timed out.") from exc
    except httpx.HTTPError as exc:
        temp_path.unlink(missing_ok=True)
        raise AssetError(f"HTTP download failed: {exc.__class__.__name__}.") from exc
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _run_gallery_dl_once(asset: dict, staging_dir: Path,
                         instagram_auth: dict | None = None) -> tuple[list[Path], dict]:
    post_url = asset["resolved_post_url"]
    target_dir = staging_dir / f"instagram_{safe_token(asset['asset_id'])}"
    target_dir.mkdir(parents=True, exist_ok=True)

    if getattr(sys, "frozen", False):
        command = [sys.executable, "--gallery-dl-helper"]
    else:
        command = [sys.executable, "-m", "gallery_dl"]

    command.extend([
        "--config-ignore",
        "--no-input",
        "--verbose",
        "--http-timeout", "30",
        "--retries", "2",
        "--sleep-request", "6-12",
        "--directory", str(target_dir),
    ])
    try:
        auth = validate_auth_config(instagram_auth)
        cookie_selector = browser_cookie_selector(auth)
    except ValueError as exc:
        raise AssetError(str(exc)) from exc
    if cookie_selector:
        command.extend(["--cookies-from-browser", cookie_selector])
    if asset.get("media_index") is not None:
        command.extend(["--range", str(asset["media_index"])])
    command.append(post_url)

    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=240, check=False
        )
    except FileNotFoundError as exc:
        raise AssetError("gallery-dl is not installed. Run: pip install -r requirements.txt") from exc
    except subprocess.TimeoutExpired as exc:
        diagnostic = _gallery_failure_diagnostic(
            str(exc.stdout or ""), str(exc.stderr or ""), None,
            time.monotonic() - started, timed_out=True
        )
        raise InstagramExtractionError(
            "Instagram extraction timed out. A single conservative retry may help.",
            diagnostic
        ) from exc

    duration = time.monotonic() - started
    candidates = [p for p in target_dir.rglob("*") if p.is_file()]
    media_files = []
    for candidate in candidates:
        try:
            classify_media(candidate, "auto")
            media_files.append(candidate)
        except AssetError:
            continue

    attempt_diagnostic = _gallery_failure_diagnostic(
        completed.stdout or "", completed.stderr or "",
        completed.returncode, duration
    )
    if completed.returncode != 0 or not media_files:
        message = (
            "Instagram extraction failed or returned no supported media. "
            f"Category: {attempt_diagnostic['error_category']}. "
            f"Details: {attempt_diagnostic['diagnostic_excerpt']}"
        )
        raise InstagramExtractionError(message, attempt_diagnostic)

    attempt_diagnostic.update({
        "error_category": None,
        "retryable": False,
        "outcome": "downloaded",
    })
    return sorted(media_files), attempt_diagnostic


def run_gallery_dl(asset: dict, staging_dir: Path,
                   instagram_auth: dict | None = None) -> list[Path]:
    """Backward-compatible one-shot gallery-dl call."""
    files, _diagnostic = _run_gallery_dl_once(asset, staging_dir, instagram_auth)
    return files


def download_instagram_with_retries(
    asset: dict,
    staging_dir: Path,
    instagram_auth: dict | None = None,
    download_policy: dict | None = None,
    status_callback=None,
) -> tuple[list[Path], list[dict]]:
    """Download one publication with at most one conservative retry."""
    policy = download_policy or {}
    max_retries = max(0, min(1, int(policy.get("max_retries", 1))))
    base_backoff = max(5, min(120, int(policy.get("backoff_base_seconds", 20))))
    max_backoff = max(base_backoff, min(600, int(policy.get("max_backoff_seconds", 300))))
    attempts = []

    for attempt_number in range(1, max_retries + 2):
        if attempt_number > 1:
            _clean_staging_target(asset, staging_dir)
        try:
            files, diagnostic = _run_gallery_dl_once(asset, staging_dir, instagram_auth)
            diagnostic["attempt"] = attempt_number
            diagnostic["outcome"] = "downloaded"
            attempts.append(diagnostic)
            return files, attempts
        except InstagramExtractionError as exc:
            diagnostic = dict(exc.diagnostic)
            diagnostic["attempt"] = attempt_number
            diagnostic["outcome"] = "failed"
            attempts.append(diagnostic)

            if attempt_number > max_retries or not diagnostic.get("retryable"):
                diagnostic["attempts"] = attempts
                raise InstagramExtractionError(str(exc), diagnostic) from exc

            category = diagnostic.get("error_category")
            retry_after = diagnostic.get("retry_after_seconds")
            if category == "rate_limited":
                if retry_after is not None and retry_after > max_backoff:
                    diagnostic["retryable"] = False
                    diagnostic["retry_skipped_reason"] = (
                        f"Retry-After requested {retry_after}s, beyond the configured "
                        f"{max_backoff}s in-process wait. Retry manually later."
                    )
                    diagnostic["attempts"] = attempts
                    raise InstagramExtractionError(
                        str(exc) + " " + diagnostic["retry_skipped_reason"],
                        diagnostic
                    ) from exc
                wait_seconds = retry_after if retry_after is not None else 60
            else:
                wait_seconds = base_backoff * (2 ** (attempt_number - 1))
            wait_seconds = max(5, min(max_backoff, int(wait_seconds)))
            diagnostic["retry_wait_seconds"] = wait_seconds
            if status_callback:
                label = "rate limit" if category == "rate_limited" else category.replace("_", " ")
                status_callback(
                    f"Instagram · {label} · retrying once in {wait_seconds}s ({asset['asset_id']})"
                )
            time.sleep(wait_seconds)

    raise InstagramExtractionError(
        "Instagram extraction failed after the configured retry.",
        {"error_category": "unknown", "attempts": attempts}
    )


def store_media_files(
    source_files: list[Path],
    asset: dict,
    image_dir: Path,
    video_dir: Path,
    hashes: dict,
    content_type: str = "",
    max_bytes: int | None = None,
) -> list[dict]:
    # Validate the entire publication before copying any files or adding hashes.
    # A malformed later carousel slide must not leave unreported files behind.
    prepared = []
    for source_path in source_files:
        if max_bytes is not None and source_path.stat().st_size > max_bytes:
            raise AssetError(f"File exceeds the configured {max_bytes // (1024 * 1024)} MB limit.")
        detected_type, extension = classify_media(source_path, asset.get("media_type", "auto"), content_type)
        prepared.append((source_path, detected_type, extension))
    saved = []
    for index, (source_path, detected_type, extension) in enumerate(prepared, start=1):
        digest = sha256_file(source_path)
        size = source_path.stat().st_size
        if digest in hashes:
            saved.append({
                "path": hashes[digest],
                "sha256": digest,
                "bytes": size,
                "media_type": detected_type,
                "status": "duplicate",
                "duplicate_of": hashes[digest],
            })
            continue

        suffix = "" if len(source_files) == 1 else f"-{index:03d}"
        filename = f"{safe_token(asset['asset_id'])}{suffix}{extension}"
        destination_dir = image_dir if detected_type == "image" else video_dir
        destination = destination_dir / filename
        counter = 2
        while destination.exists():
            destination = destination_dir / f"{safe_token(asset['asset_id'])}{suffix}-{counter}{extension}"
            counter += 1
        shutil.copy2(source_path, destination)
        relative_path = destination.relative_to(image_dir.parent).as_posix()
        hashes[digest] = relative_path
        saved.append({
            "path": relative_path,
            "sha256": digest,
            "bytes": size,
            "media_type": detected_type,
            "status": "downloaded",
        })
    return saved


def build_report(job_id: str, results: list[dict]) -> str:
    files_saved = sum(1 for r in results for f in r.get("files", []) if f.get("status") == "downloaded")
    duplicates = sum(1 for r in results for f in r.get("files", []) if f.get("status") == "duplicate")
    assets_downloaded = sum(1 for r in results if r["status"] == "downloaded")
    failed = sum(1 for r in results if r["status"] == "failed")
    deferred = sum(1 for r in results if r["status"] == "deferred")
    lines = [
        f"# Extraction Report — {job_id}",
        "",
        f"- Generated: {utc_now()}",
        f"- Input assets: {len(results)}",
        f"- Assets downloaded: {assets_downloaded}",
        f"- Files saved: {files_saved}",
        f"- Duplicate files: {duplicates}",
        f"- Failed assets: {failed}",
        f"- Deferred assets: {deferred}",
        "",
        "## Results",
        "",
        "| Asset ID | Source | Status | Files | Retries | Failure category | Notes |",
        "|---|---|---|---:|---:|---|---|",
    ]
    for result in results:
        files_count = len(result.get("files", []))
        diagnostics = result.get("diagnostics") or []
        retries = max(0, len(diagnostics) - 1)
        category = result.get("failure_category") or "—"
        note = (result.get("message") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {result['asset_id']} | {result['source_type']} | {result['status']} | "
            f"{files_count} | {retries} | {category} | {note} |"
        )
    lines.extend([
        "",
        "## Instagram diagnostics",
        "",
        "Attempt-level diagnostics are stored in `metadata/download_results.json`. "
        "Cookie values and authorization headers are redacted. HTTP status and response "
        "metadata are included only when gallery-dl exposes them in its verbose output.",
        "",
        "## Reuse and access reminder",
        "",
        "Successful download does not establish copyright ownership or permission for commercial reuse. "
        "Check the source and the rights information before publishing.",
        "",
    ])
    return "\n".join(lines)



def create_zip(package_dir: Path, zip_path: Path) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=zip_path.stem + ".", suffix=".zip.tmp", dir=zip_path.parent)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(package_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=Path(package_dir.name) / path.relative_to(package_dir))
        os.replace(temporary, zip_path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def run_job(
    manifest_data: dict,
    assets: list[dict],
    output_dir: Path,
    max_bytes: int,
    progress_callback=None,
    instagram_auth: dict | None = None,
    download_policy: dict | None = None,
    status_callback=None,
) -> tuple[Path, Path, list[dict]]:
    policy = dict(download_policy or {})
    try:
        instagram_delay = max(0, min(60, int(policy.get("instagram_delay_seconds", 12))))
    except (TypeError, ValueError):
        instagram_delay = 12
    try:
        max_retries = max(0, min(1, int(policy.get("max_retries", 1))))
    except (TypeError, ValueError):
        max_retries = 1
    policy["max_retries"] = max_retries
    policy.setdefault("backoff_base_seconds", 20)
    policy.setdefault("max_backoff_seconds", 300)
    try:
        stop_after = max(2, min(4, int(policy.get("stop_after_consecutive_failures", 2))))
    except (TypeError, ValueError):
        stop_after = 2

    requested_job_id = manifest_data.get("job_id") or f"asset-job-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    job_id = safe_token(requested_job_id, "asset-job")
    package_dir = output_dir / job_id
    suffix = 2
    while package_dir.exists() or (output_dir / f"{package_dir.name}.zip").exists():
        package_dir = output_dir / f"{job_id}-{suffix}"
        suffix += 1
    job_id = package_dir.name

    image_dir = package_dir / "images"
    video_dir = package_dir / "videos"
    metadata_dir = package_dir / "metadata"
    staging_dir = package_dir / ".staging"
    for folder in (image_dir, video_dir, metadata_dir, staging_dir):
        folder.mkdir(parents=True, exist_ok=True)

    hashes: dict[str, str] = {}
    results = []
    last_instagram_finish = None
    consecutive_access_failures = 0
    instagram_circuit_open = False
    circuit_reason = ""

    def emit_status(message: str):
        if status_callback:
            try:
                status_callback(message)
            except Exception:
                pass

    for asset in assets:
        result = {
            "asset_id": asset["asset_id"],
            "source_type": asset["source_type"],
            "source_url": asset.get("resolved_post_url") or asset.get("media_url"),
            "source_page_url": asset.get("source_page_url"),
            "rights_status": asset.get("rights_status", "unknown"),
            "status": "failed",
            "files": [],
            "message": "",
            "diagnostics": [],
            "retry_count": 0,
        }

        if asset["source_type"] == "instagram" and instagram_circuit_open:
            result["status"] = "deferred"
            result["failure_category"] = "instagram_access_circuit_open"
            result["deferred_reason"] = circuit_reason
            result["message"] = (
                "Deferred without another Instagram request because repeated access failures "
                f"opened the safety circuit: {circuit_reason}"
            )
            results.append(result)
            if progress_callback:
                try:
                    progress_callback(len(results), len(assets), result)
                except Exception:
                    pass
            continue

        try:
            if asset["source_type"] == "instagram":
                if last_instagram_finish is not None and instagram_delay:
                    elapsed = time.monotonic() - last_instagram_finish
                    remaining = instagram_delay - elapsed
                    if remaining > 0:
                        wait_seconds = int(round(remaining))
                        emit_status(f"Instagram · waiting {wait_seconds}s before next publication")
                        time.sleep(remaining)
                auth_mode = (instagram_auth or {}).get("mode", "anonymous")
                auth_label = "Firefox session selected" if auth_mode == "firefox" else "anonymous access"
                emit_status(f"Instagram · {auth_label} · downloading {asset['asset_id']}")

                source_files, attempt_diagnostics = download_instagram_with_retries(
                    asset, staging_dir,
                    instagram_auth=instagram_auth,
                    download_policy=policy,
                    status_callback=emit_status,
                )
                result["diagnostics"] = attempt_diagnostics
                result["retry_count"] = max(0, len(attempt_diagnostics) - 1)
                result["files"] = store_media_files(
                    source_files, asset, image_dir, video_dir, hashes, max_bytes=max_bytes
                )
                consecutive_access_failures = 0
            else:
                temp_path, content_type = fetch_direct_url(asset, staging_dir, max_bytes)
                try:
                    result["files"] = store_media_files(
                        [temp_path], asset, image_dir, video_dir, hashes, content_type
                    )
                finally:
                    temp_path.unlink(missing_ok=True)

            result["status"] = "duplicate" if result["files"] and all(
                f["status"] == "duplicate" for f in result["files"]
            ) else "downloaded"
            if result["retry_count"]:
                result["message"] = f"OK (recovered after {result['retry_count']} retry)."
            else:
                result["message"] = "OK" if result["status"] == "downloaded" else "Content matched an earlier asset."

        except InstagramExtractionError as exc:
            diagnostic = dict(exc.diagnostic or {})
            attempts = diagnostic.get("attempts")
            if attempts:
                result["diagnostics"] = attempts
            elif diagnostic:
                result["diagnostics"] = [diagnostic]
            result["retry_count"] = max(0, len(result["diagnostics"]) - 1)
            category = diagnostic.get("error_category", "extraction_error")
            result["failure_category"] = category
            result["message"] = str(exc)

            blocking_categories = {
                "rate_limited", "auth_or_challenge",
                "unexpected_response", "server_error",
            }
            if category in blocking_categories:
                consecutive_access_failures += 1
            else:
                consecutive_access_failures = 0

            if category == "rate_limited" or consecutive_access_failures >= stop_after:
                instagram_circuit_open = True
                circuit_reason = (
                    f"{category.replace('_', ' ')} persisted across "
                    f"{consecutive_access_failures} consecutive publication(s)"
                )
                emit_status("Instagram · access circuit open · remaining Instagram items deferred")

        except AssetError as exc:
            result["message"] = str(exc)
        except Exception as exc:
            result["message"] = f"Unexpected error: {exc.__class__.__name__}: {exc}"

        if asset["source_type"] == "instagram":
            last_instagram_finish = time.monotonic()

        results.append(result)
        if progress_callback:
            try:
                progress_callback(len(results), len(assets), result)
            except Exception:
                pass

    shutil.rmtree(staging_dir, ignore_errors=True)
    (metadata_dir / "asset_manifest.json").write_text(
        json.dumps(manifest_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    profile_discovery = manifest_data.get("profile_discovery")
    if isinstance(profile_discovery, dict):
        (metadata_dir / "profile_discovery.json").write_text(
            json.dumps(profile_discovery, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    results_payload = {
        "tool": "GTL AI Asset Downloader",
        "version": VERSION,
        "job_id": job_id,
        "generated_at": utc_now(),
        "download_policy": {
            "instagram_delay_seconds": instagram_delay,
            "max_retries_per_publication": max_retries,
            "stop_after_consecutive_failures": stop_after,
            "instagram_circuit_opened": instagram_circuit_open,
            "instagram_circuit_reason": circuit_reason or None,
        },
        "summary": {
            "assets_total": len(results),
            "assets_downloaded": sum(1 for r in results if r["status"] == "downloaded"),
            "assets_duplicate_only": sum(1 for r in results if r["status"] == "duplicate"),
            "assets_failed": sum(1 for r in results if r["status"] == "failed"),
            "assets_deferred": sum(1 for r in results if r["status"] == "deferred"),
            "files_saved": sum(1 for r in results for f in r.get("files", []) if f["status"] == "downloaded"),
            "files_duplicate": sum(1 for r in results for f in r.get("files", []) if f["status"] == "duplicate"),
        },
        "results": results,
    }
    (metadata_dir / "download_results.json").write_text(
        json.dumps(results_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    with (metadata_dir / "source_manifest.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "asset_id", "source_type", "source_url", "source_page_url",
            "rights_status", "status", "saved_files", "retry_count",
            "failure_category", "message"
        ])
        writer.writeheader()
        for result in results:
            writer.writerow({
                "asset_id": result["asset_id"],
                "source_type": result["source_type"],
                "source_url": result.get("source_url", ""),
                "source_page_url": result.get("source_page_url", ""),
                "rights_status": result.get("rights_status", "unknown"),
                "status": result["status"],
                "saved_files": ";".join(f["path"] for f in result.get("files", [])),
                "retry_count": result.get("retry_count", 0),
                "failure_category": result.get("failure_category", ""),
                "message": result.get("message", ""),
            })

    (package_dir / "extraction_report.md").write_text(build_report(job_id, results), encoding="utf-8")
    zip_path = output_dir / f"{job_id}.zip"
    create_zip(package_dir, zip_path)
    return package_dir, zip_path, results

