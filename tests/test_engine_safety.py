"""Regressions for validation, media limits, and package preservation."""
import json
import io
import zipfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import httpx
from core import engine
from core.preferences import read_json, write_json
from core.firefox_profiles import redact_sensitive_text


class EngineSafetyTests(unittest.TestCase):
    def test_direct_media_pipeline_saves_deduplicates_and_packages(self):
        payload = io.BytesIO()
        Image.new("RGB", (24, 24), "teal").save(payload, format="PNG")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = [{"asset_id": asset_id, "source_type": "direct_url", "media_url": "https://example.com/image.png"}
                      for asset_id in ("first", "second")]
            real_client = httpx.Client
            transport = httpx.MockTransport(lambda request: httpx.Response(200, content=payload.getvalue(),
                                           headers={"content-type": "image/png"}))
            def client_factory(**kwargs):
                return real_client(transport=transport, **kwargs)
            with patch("core.engine.httpx.Client", side_effect=client_factory), \
                 patch("core.engine.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
                package, archive, results = engine.run_job({"schema_version": "1.0", "assets": assets}, assets, root, 1024)
            self.assertEqual([r["status"] for r in results], ["downloaded", "duplicate"])
            self.assertEqual(len(list((package / "images").iterdir())), 1)
            self.assertFalse((package / ".staging").exists())
            with zipfile.ZipFile(archive) as zipped:
                self.assertIsNone(zipped.testzip())
                metadata = json.loads(zipped.read(f"{package.name}/metadata/download_results.json"))
            self.assertEqual(metadata["summary"]["files_saved"], 1)
            self.assertEqual(metadata["summary"]["files_duplicate"], 1)

    def test_direct_redirect_to_private_host_is_blocked_before_request(self):
        requested = []
        def respond(request):
            requested.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private.png"})
        with tempfile.TemporaryDirectory() as temporary:
            client = httpx.Client(transport=httpx.MockTransport(respond))
            with patch("core.engine.httpx.Client", return_value=client), \
                 patch("core.engine.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
                with self.assertRaises(engine.AssetError):
                    engine.fetch_direct_url({"asset_id": "a", "media_url": "https://example.com/a.png"}, Path(temporary), 1024)
            self.assertEqual(requested, ["https://example.com/a.png"])

    def test_oversized_direct_download_leaves_no_partial_file(self):
        class LargeStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b"first"
                yield b"this chunk exceeds the limit"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=LargeStream())))
            with patch("core.engine.httpx.Client", return_value=client), \
                 patch("core.engine.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
                with self.assertRaises(engine.AssetError):
                    engine.fetch_direct_url({"asset_id": "a", "media_url": "https://example.com/a.png"}, root, 10)
            self.assertEqual(list(root.iterdir()), [])

    def test_windows_device_names_are_made_safe(self):
        for token in ("CON", "con.txt", "LPT1", "aux", "NUL"):
            self.assertTrue(engine.safe_token(token).startswith("asset-"))

    def test_failed_zip_write_preserves_previous_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "package"
            package.mkdir()
            (package / "file.txt").write_text("data")
            archive = root / "package.zip"
            archive.write_bytes(b"previous")
            with patch("core.engine.zipfile.ZipFile.write", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    engine.create_zip(package, archive)
            self.assertEqual(archive.read_bytes(), b"previous")
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["package", "package.zip"])

    def test_malformed_urls_produce_reportable_asset_errors(self):
        for url in ("https://example.com:bad/image.jpg", "https://[broken/image.jpg",
                    "https://example.com\r\nX-Test: bad", "https://user:password@example.com/a.jpg"):
            with self.subTest(url=url), self.assertRaises(engine.AssetError):
                engine.validate_http_url(url)

    def test_malformed_manifest_types_are_reportable(self):
        for key, value in (("source_type", []), ("media_type", {})):
            asset = {"asset_id": "a", "source_type": "direct_url", "media_url": "https://example.com/a.png", key: value}
            with self.subTest(key=key), self.assertRaises(engine.AssetError):
                engine.validate_manifest({"schema_version": "1.0", "assets": [asset]})

    def test_instagram_publication_subpaths_are_rejected(self):
        with self.assertRaises(engine.AssetError):
            engine.resolve_instagram_post_url({"post_url": "https://www.instagram.com/p/ABC/comments/"})

    def test_html_with_video_filename_and_content_type_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pretend.mp4"
            path.write_text("<html>Please sign in</html>")
            with self.assertRaises(engine.AssetError):
                engine.classify_media(path, "video", "video/mp4")

    def test_carousel_is_validated_before_any_slide_is_copied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_dir, video_dir = root / "images", root / "videos"
            image_dir.mkdir()
            video_dir.mkdir()
            valid, invalid = root / "valid.png", root / "invalid.png"
            Image.new("RGB", (10, 10), "teal").save(valid)
            invalid.write_text("<html>not an image</html>")
            hashes = {}
            with self.assertRaises(engine.AssetError):
                engine.store_media_files([valid, invalid], {"asset_id": "carousel"}, image_dir, video_dir, hashes)
            self.assertEqual(list(image_dir.iterdir()), [])
            self.assertEqual(hashes, {})

    def test_instagram_media_obeys_configured_file_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "slide.png"
            Image.new("RGB", (20, 20), "teal").save(media)
            asset = {"asset_id": "ig-001", "source_type": "instagram", "resolved_post_url": "https://www.instagram.com/p/ABC/"}
            with patch("core.engine.download_instagram_with_retries", return_value=([media], [])):
                package, _, results = engine.run_job({"job_id": "limit"}, [asset], root / "output", max_bytes=10)
            self.assertEqual(results[0]["status"], "failed")
            self.assertIn("limit", results[0]["message"])
            self.assertEqual(list((package / "images").iterdir()), [])

    def test_existing_zip_is_never_overwritten_when_folder_was_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = root / "previous.zip"
            existing.write_bytes(b"existing package")
            package, archive, _ = engine.run_job({"job_id": "previous"}, [], root, 100)
            self.assertEqual(package.name, "previous-2")
            self.assertEqual(archive.name, "previous-2.zip")
            self.assertEqual(existing.read_bytes(), b"existing package")

    def test_utf8_bom_manifest_is_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(json.dumps({"schema_version": "1.0", "assets": [
                {"asset_id": "a", "source_type": "direct_url", "media_url": "https://example.com/a.png"}
            ]}), encoding="utf-8-sig")
            self.assertEqual(len(engine.load_manifest(path)[1]), 1)


class PreferenceStoreTests(unittest.TestCase):
    def test_quoted_headers_are_redacted_from_diagnostics(self):
        raw = '''headers: {'Authorization': 'Bearer auth-secret', 'Cookie': 'other=cookie-secret'}'''
        safe = redact_sensitive_text(raw)
        self.assertNotIn("auth-secret", safe)
        self.assertNotIn("cookie-secret", safe)
        self.assertIn("headers", safe)

    def test_json_session_values_are_redacted(self):
        raw = '''{"sessionid": "session-secret", "csrftoken": "csrf-secret", "status": "failed"}'''
        safe = redact_sensitive_text(raw)
        self.assertNotIn("session-secret", safe)
        self.assertNotIn("csrf-secret", safe)
        self.assertIn('"status": "failed"', safe)

    def test_wrong_json_shape_and_corrupt_files_fall_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            for payload in ("[]", "null", "42", "not json"):
                path.write_text(payload)
                self.assertEqual(read_json(path, dict, {}), {})

    def test_failed_replace_preserves_existing_file_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            write_json(path, {"profile": "selected"})
            with patch("core.preferences.os.replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    write_json(path, {"profile": "lost"})
            self.assertEqual(read_json(path, dict, {}), {"profile": "selected"})
            self.assertEqual(list(path.parent.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
