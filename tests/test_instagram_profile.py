import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.firefox_profiles import (
    browser_cookie_selector,
    discover_firefox_profiles,
    validate_auth_config,
)
from core.instagram_profile import (
    _parse_gallery_json,
    _normalize_records,
    _redact_sensitive,
    _version_tuple,
    _run_discovery,
    discover_profile,
)
from core.engine import AssetError


class FirefoxProfileTests(unittest.TestCase):
    def test_auto_firefox_selector(self):
        self.assertEqual(browser_cookie_selector({"mode": "firefox"}), "firefox/instagram.com")

    def test_explicit_firefox_profile_selector(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp) / "profile"
            profile.mkdir()
            (profile / "cookies.sqlite").write_bytes(b"test")
            self.assertEqual(
                browser_cookie_selector({"mode": "firefox", "profile": str(profile)}),
                f"firefox/instagram.com:{profile}",
            )

    def test_missing_firefox_cookie_database_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                browser_cookie_selector({"mode": "firefox", "profile": temp})

    def test_anonymous_mode_does_not_use_browser_cookies(self):
        self.assertIsNone(browser_cookie_selector({"mode": "anonymous"}))

    def test_invalid_auth_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_auth_config({"mode": "chrome"})

    def test_discover_firefox_profiles_from_profiles_ini(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Mozilla" / "Firefox"
            profiles_dir = root / "Profiles" / "abc.default-release"
            profiles_dir.mkdir(parents=True)
            (profiles_dir / "cookies.sqlite").write_bytes(b"sqlite-placeholder")
            (root / "profiles.ini").write_text(
                "[Profile0]\nName=default-release\nIsRelative=1\n"
                "Path=Profiles/abc.default-release\nDefault=1\n",
                encoding="utf-8",
            )
            with patch("core.firefox_profiles.firefox_root", return_value=root):
                found = discover_firefox_profiles()
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["name"], "default-release")
            self.assertTrue(found[0]["is_default"])
            self.assertTrue(Path(found[0]["cookies_db"]).is_file())


class InstagramProfileDiscoveryTests(unittest.TestCase):
    def test_parse_pretty_printed_gallery_json(self):
        payload = [
            ["instagram", "post", 123, {
                "shortcode": "AbC_123",
                "post_date": 1795000000,
                "post_caption": "A caption",
                "media_id": "555",
            }]
        ]
        parsed = _parse_gallery_json(json.dumps(payload, indent=2))
        self.assertTrue(any(r.get("shortcode") == "AbC_123" for r in parsed))

    def test_parse_concatenated_pretty_json_values(self):
        first = json.dumps(["instagram", "post", 1, {"shortcode": "FIRST"}], indent=2)
        second = json.dumps(["instagram", "post", 2, {"shortcode": "SECOND"}], indent=2)
        parsed = _parse_gallery_json(first + "\n" + second)
        self.assertEqual({row["shortcode"] for row in parsed}, {"FIRST", "SECOND"})

    def test_normalize_groups_carousel_media_by_shortcode(self):
        rows = [
            {"shortcode": "CAROUSEL1", "post_date": 1795000000, "media_id": "1", "caption": "Carousel"},
            {"shortcode": "CAROUSEL1", "post_date": 1795000000, "media_id": "2", "caption": "Carousel"},
            {"shortcode": "POST2", "post_date": 1794000000, "media_id": "3"},
        ]
        normalized = _normalize_records(rows, "example", "posts")
        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized[0]["shortcode"], "CAROUSEL1")
        self.assertEqual(normalized[0]["media_count"], 2)

    def test_gallery_dl_version_parser_and_sensitive_redaction(self):
        self.assertEqual(_version_tuple("gallery-dl 1.32.22-dev"), (1, 32, 22))
        redacted = _redact_sensitive(
            "Found 'lsd=secret123' & 'fb_dtsg=anotherSecret:0:0' "
            "Cookie: sessionid=do-not-log"
        )
        for secret in ("secret123", "anotherSecret", "do-not-log"):
            self.assertNotIn(secret, redacted)
        self.assertIn("<REDACTED>", redacted)
        from core.firefox_profiles import redact_sensitive_text
        safe = redact_sensitive_text(
            "[request] Cookie: sessionid=private-value "
            "https://cdn.example/file.mp4?X-Amz-Signature=private-signature"
        )
        self.assertNotIn("private-value", safe)
        self.assertNotIn("private-signature", safe)

    @patch("core.instagram_profile.subprocess.run")
    def test_runner_uses_firefox_browser_cookies(self, run):
        run.return_value = Mock(
            returncode=0,
            stdout='["instagram","post",1,{"shortcode":"X1","post_date":1795000000}]\n',
            stderr="[cookies][info] Extracted 12 cookies from Firefox",
        )
        records, diagnostic = _run_discovery(
            "https://www.instagram.com/example/", "posts", 50, 60,
            username="example", auth_config={"mode": "firefox"},
        )
        args = run.call_args.args[0]
        self.assertIn("--cookies-from-browser", args)
        self.assertEqual(args[args.index("--cookies-from-browser") + 1], "firefox/instagram.com")
        self.assertEqual(args[-1], "https://www.instagram.com/example/")
        self.assertEqual(records[0]["shortcode"], "X1")

    @patch("core.instagram_profile.subprocess.run")
    def test_runner_uses_selected_firefox_profile(self, run):
        run.return_value = Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp) / "ff-profile"
            profile.mkdir()
            (profile / "cookies.sqlite").write_bytes(b"test")
            _run_discovery(
                "https://www.instagram.com/example/", "posts", 50, 60,
                username="example",
                auth_config={"mode": "firefox", "profile": str(profile)},
            )
            args = run.call_args.args[0]
            selector = args[args.index("--cookies-from-browser") + 1]
            self.assertEqual(selector, f"firefox/instagram.com:{profile}")

    @patch("core.instagram_profile.subprocess.run")
    def test_anonymous_runner_does_not_pass_cookie_option(self, run):
        run.return_value = Mock(returncode=0, stdout="", stderr="")
        _run_discovery(
            "https://www.instagram.com/example/", "posts", 50, 60,
            username="example", auth_config={"mode": "anonymous"},
        )
        args = run.call_args.args[0]
        self.assertNotIn("--cookies-from-browser", args)

    @patch("core.instagram_profile._run_discovery")
    @patch("core.instagram_profile._gallery_dl_version", return_value="gallery-dl 1.32.22-dev")
    def test_discovery_uses_exact_username_url_not_synthetic_id(self, version, run):
        posts = [{
            "shortcode": "A", "publication_type": "post",
            "post_url": "https://www.instagram.com/p/A/", "timestamp": 30,
            "media_count": 2, "profile_url": "https://www.instagram.com/example/",
        }]
        reels = [{
            "shortcode": "B", "publication_type": "reel",
            "post_url": "https://www.instagram.com/reel/B/", "timestamp": 20,
            "media_count": 1, "profile_url": "https://www.instagram.com/example/",
        }]
        run.side_effect = [(posts, "posts OK"), (reels, "reels OK")]
        result = discover_profile(
            "https://www.instagram.com/example/", limit=50,
            auth_config={"mode": "firefox"},
        )
        self.assertIsNone(result["profile_id"])
        self.assertEqual(result["discovered_count"], 2)
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertEqual(call.args[0], "https://www.instagram.com/example/")
            self.assertEqual(call.kwargs["auth_config"]["mode"], "firefox")

    @patch("core.instagram_profile._run_discovery")
    @patch("core.instagram_profile._gallery_dl_version", return_value="gallery-dl 1.32.22-dev")
    def test_401_is_reported_as_auth_or_request_rejection_not_empty_profile(self, version, run):
        run.side_effect = [
            ([], "posts: HTTP 401 Unauthorized"),
            ([], "reels: HTTP 401 Unauthorized"),
        ]
        with self.assertRaises(AssetError) as caught:
            discover_profile(
                "https://www.instagram.com/example/",
                auth_config={"mode": "firefox"},
            )
        self.assertIn("returned HTTP 401", str(caught.exception))
        self.assertIn("not proof", str(caught.exception))

    @patch("core.instagram_profile._run_discovery")
    @patch("core.instagram_profile._gallery_dl_version", return_value="gallery-dl 1.32.22-dev")
    def test_graphql_operation_error_is_distinguished_from_cookie_failure(self, version, run):
        run.side_effect = [
            ([], "KeyError PolarisProfilePostsTabContentQuery_connection"),
            ([], "KeyError PolarisProfileReelsTabContentQuery_connection"),
        ]
        with self.assertRaises(AssetError) as caught:
            discover_profile(
                "https://www.instagram.com/example/",
                auth_config={"mode": "firefox"},
            )
        self.assertIn("GraphQL operation", str(caught.exception))
        self.assertIn("valid Firefox session may not fix", str(caught.exception))

    @patch("core.engine.subprocess.run")
    def test_individual_instagram_download_uses_firefox_cookies(self, run):
        from PIL import Image
        from core.engine import run_gallery_dl

        def fake_run(command, **kwargs):
            target_dir = Path(command[command.index("--directory") + 1])
            Image.new("RGB", (2, 2)).save(target_dir / "sample.png")
            return Mock(returncode=0, stdout="", stderr="")

        run.side_effect = fake_run
        with tempfile.TemporaryDirectory() as temp:
            asset = {
                "asset_id": "ig-test-001",
                "resolved_post_url": "https://www.instagram.com/p/ABC123/",
            }
            files = run_gallery_dl(asset, Path(temp), instagram_auth={"mode": "firefox"})
            args = run.call_args.args[0]
            self.assertIn("--cookies-from-browser", args)
            self.assertEqual(args[args.index("--cookies-from-browser") + 1], "firefox/instagram.com")
            self.assertEqual(len(files), 1)

    def test_profile_root_is_rejected(self):
        with self.assertRaises(AssetError):
            discover_profile("https://www.instagram.com/", limit=50)

    def test_limit_out_of_range_is_rejected(self):
        with self.assertRaises(AssetError):
            discover_profile("https://www.instagram.com/example/", limit=0)


if __name__ == "__main__":
    unittest.main()
