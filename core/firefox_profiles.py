"""Local Firefox profile discovery for gallery-dl browser-cookie integration.

Only profile metadata and the existence of cookies.sqlite are inspected here.
Cookie values are never read, copied, exported, logged, or transmitted by this module.
gallery-dl reads the selected Firefox cookie database locally when invoked.
"""
from __future__ import annotations

import configparser
import os
from pathlib import Path


def firefox_root() -> Path | None:
    """Return the Firefox application-data directory on Windows."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Mozilla" / "Firefox"
    return None


def discover_firefox_profiles() -> list[dict]:
    """Discover Firefox profiles that have a local cookies.sqlite database."""
    root = firefox_root()
    if root is None:
        return []

    ini_path = root / "profiles.ini"
    if not ini_path.is_file():
        return []

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(ini_path, encoding="utf-8")
    except (OSError, configparser.Error):
        return []

    install_defaults = set()
    for section in parser.sections():
        if section.lower().startswith("install"):
            default_path = parser.get(section, "Default", fallback="").strip()
            if default_path:
                candidate = Path(default_path)
                if not candidate.is_absolute():
                    candidate = root / candidate
                install_defaults.add(str(candidate.resolve()).casefold())

    profiles = []
    for section in parser.sections():
        if not section.lower().startswith("profile"):
            continue
        name = parser.get(section, "Name", fallback=section).strip() or section
        raw_path = parser.get(section, "Path", fallback="").strip()
        if not raw_path:
            continue

        try:
            is_relative = parser.getboolean(section, "IsRelative", fallback=True)
            profile_default = parser.getboolean(section, "Default", fallback=False)
        except ValueError:
            continue
        profile_path = Path(raw_path)
        if is_relative:
            profile_path = root / profile_path
        try:
            profile_path = profile_path.resolve()
        except OSError:
            continue

        cookies_db = profile_path / "cookies.sqlite"
        if not profile_path.is_dir() or not cookies_db.is_file():
            continue

        is_default = (
            profile_default
            or str(profile_path).casefold() in install_defaults
        )
        profiles.append({
            "name": name,
            "path": str(profile_path),
            "is_default": bool(is_default),
            "cookies_db": str(cookies_db),
        })

    # Prefer the profile Firefox itself marks as default, then stable name order.
    profiles.sort(key=lambda item: (not item["is_default"], item["name"].casefold()))
    return profiles


def browser_cookie_selector(auth_config: dict | None) -> str | None:
    """Return gallery-dl's --cookies-from-browser selector, or None for anonymous mode."""
    config = auth_config or {}
    mode = str(config.get("mode", "anonymous")).strip().lower()
    if mode != "firefox":
        return None

    profile = str(config.get("profile") or "").strip()
    if not profile:
        return "firefox/instagram.com"

    profile_path = Path(profile)
    if not profile_path.is_dir() or not (profile_path / "cookies.sqlite").is_file():
        raise ValueError(
            "The selected Firefox profile is no longer available or has no cookies.sqlite. "
            "Open Settings, refresh Firefox profiles, and select the profile where Instagram is signed in."
        )
    # gallery-dl accepts an absolute profile directory after the browser/domain selector.
    return f"firefox/instagram.com:{profile_path}"


def validate_auth_config(auth_config: dict | None) -> dict:
    """Normalize and validate the persisted authentication settings."""
    config = dict(auth_config or {})
    mode = str(config.get("mode", "anonymous")).strip().lower()
    if mode not in {"anonymous", "firefox"}:
        raise ValueError("Instagram authentication mode must be 'anonymous' or 'firefox'.")
    profile = str(config.get("profile") or "").strip()
    normalized = {"mode": mode, "profile": profile}
    browser_cookie_selector(normalized)
    return normalized


def redact_sensitive_text(text: str) -> str:
    """Redact browser cookies, auth headers, session values and signed URL tokens."""
    import re

    text = re.sub(
        r"(?im)^([^\r\n]*?\b(?:cookie|set-cookie|authorization)\s*:\s*).*$",
        r"\1<REDACTED>",
        text,
    )
    text = re.sub(
        r'''(?i)(["'](?:cookie|set-cookie|authorization)["']\s*:\s*)(["'])(.*?)\2''',
        lambda match: match[1] + match[2] + "<REDACTED>" + match[2],
        text,
    )
    text = re.sub(
        r'''(?i)(\b(?:sessionid|csrftoken|ds_user_id|ig_did|mid|rur|lsd|fb_dtsg|csrf_token)'''
        r'''["']?\s*[=:]\s*["']?)([^\s&,'"}]+)''',
        r"\1<REDACTED>",
        text,
    )
    text = re.sub(
        r"(?i)([?&](?:access_token|token|signature|sig|policy|key-pair-id|"
        r"x-amz-signature|x-amz-credential|x-amz-security-token|expires)=)"
        r"[^&\s]+",
        r"\1<REDACTED>",
        text,
    )
    return text
