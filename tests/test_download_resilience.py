import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import engine


class InstagramDownloadResilienceTests(unittest.TestCase):
    def test_classifies_json_decode_as_retryable_unexpected_response(self):
        diagnostic = engine._gallery_failure_diagnostic(
            "",
            "[instagram][error] JSONDecodeError: Expecting value: line 1 column 1 (char 0)",
            1,
            0.42,
        )
        self.assertEqual(diagnostic["error_category"], "unexpected_response")
        self.assertTrue(diagnostic["retryable"])

    def test_classifies_rate_limit_and_parses_retry_after(self):
        diagnostic = engine._gallery_failure_diagnostic(
            "",
            "HTTP/1.1 429 Too Many Requests\nRetry-After: 37\nContent-Type: text/html\nContent-Length: 81",
            1,
            0.3,
        )
        self.assertEqual(diagnostic["http_status"], 429)
        self.assertEqual(diagnostic["error_category"], "rate_limited")
        self.assertEqual(diagnostic["retry_after_seconds"], 37)
        self.assertEqual(diagnostic["content_type"], "text/html")
        self.assertEqual(diagnostic["response_bytes"], 81)

    def test_classifies_auth_challenge_as_non_retryable(self):
        diagnostic = engine._gallery_failure_diagnostic(
            "", "HTTP/2 403 Forbidden checkpoint_required", 1, 0.1
        )
        self.assertEqual(diagnostic["error_category"], "auth_or_challenge")
        self.assertFalse(diagnostic["retryable"])

    @patch("core.engine.time.sleep")
    @patch("core.engine._run_gallery_dl_once")
    def test_unexpected_response_gets_one_backoff_retry_then_succeeds(self, run_once, sleep):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "instagram_asset-1" / "file.jpg"
            def attempt_side_effect(*args, **kwargs):
                if run_once.call_count == 1:
                    raise engine.InstagramExtractionError(
                        "JSONDecodeError",
                        {"error_category": "unexpected_response", "retryable": True},
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"placeholder")
                return [target], {"duration_seconds": 0.2, "browser_cookies_loaded": True}
            run_once.side_effect = attempt_side_effect
            files, attempts = engine.download_instagram_with_retries(
                {"asset_id": "asset-1", "resolved_post_url": "https://www.instagram.com/p/ABC/"},
                Path(temp),
                instagram_auth={"mode": "firefox"},
                download_policy={"max_retries": 1, "backoff_base_seconds": 20},
            )
            self.assertEqual(len(files), 1)
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[-1]["attempt"], 2)
            sleep.assert_called_once_with(20)

    @patch("core.engine.time.sleep")
    @patch("core.engine._run_gallery_dl_once")
    def test_long_retry_after_is_not_violated_by_early_retry(self, run_once, sleep):
        run_once.side_effect = engine.InstagramExtractionError(
            "HTTP 429",
            {
                "error_category": "rate_limited",
                "retryable": True,
                "retry_after_seconds": 900,
            },
        )
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(engine.InstagramExtractionError) as caught:
                engine.download_instagram_with_retries(
                    {"asset_id": "asset-1", "resolved_post_url": "https://www.instagram.com/p/ABC/"},
                    Path(temp),
                    instagram_auth={"mode": "firefox"},
                    download_policy={"max_retries": 1, "max_backoff_seconds": 300},
                )
        self.assertIn("Retry-After requested 900s", str(caught.exception))
        self.assertEqual(run_once.call_count, 1)
        sleep.assert_not_called()

    @patch("core.engine._run_gallery_dl_once")
    def test_auth_failure_is_not_retried(self, run_once):
        run_once.side_effect = engine.InstagramExtractionError(
            "Login challenge",
            {"error_category": "auth_or_challenge", "retryable": False},
        )
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(engine.InstagramExtractionError):
                engine.download_instagram_with_retries(
                    {"asset_id": "asset-1", "resolved_post_url": "https://www.instagram.com/p/ABC/"},
                    Path(temp),
                    instagram_auth={"mode": "firefox"},
                    download_policy={"max_retries": 1},
                )
        self.assertEqual(run_once.call_count, 1)

    @patch("core.engine.fetch_direct_url")
    @patch("core.engine.download_instagram_with_retries")
    @patch("core.engine.time.sleep")
    def test_circuit_breaker_defers_remaining_instagram_but_direct_urls_continue(
        self, sleep, download, fetch_direct
    ):
        download.side_effect = engine.InstagramExtractionError(
            "JSONDecodeError",
            {
                "error_category": "unexpected_response",
                "retryable": True,
                "attempts": [
                    {"attempt": 1, "error_category": "unexpected_response"},
                    {"attempt": 2, "error_category": "unexpected_response"},
                ],
            },
        )
        assets = [
            {
                "asset_id": f"instagram-{i}",
                "source_type": "instagram",
                "resolved_post_url": f"https://www.instagram.com/p/ABC{i}/",
            }
            for i in range(1, 5)
        ]
        assets.append({
            "asset_id": "direct-after-circuit",
            "source_type": "direct_url",
            "media_url": "https://cdn.example.com/image.jpg",
        })

        def fake_direct_fetch(asset, staging_dir, max_bytes):
            from PIL import Image
            path = Path(staging_dir) / "direct-test.jpg"
            Image.new("RGB", (3, 3)).save(path, format="JPEG")
            return path, "image/jpeg"

        fetch_direct.side_effect = fake_direct_fetch
        with tempfile.TemporaryDirectory() as temp:
            package_dir, zip_path, results = engine.run_job(
                {"schema_version": "1.0", "job_id": "test-circuit"},
                assets,
                Path(temp),
                50 * 1024 * 1024,
                instagram_auth={"mode": "firefox"},
                download_policy={
                    "instagram_delay_seconds": 0,
                    "max_retries": 0,
                    "stop_after_consecutive_failures": 2,
                },
            )
            self.assertEqual([r["status"] for r in results],
                             ["failed", "failed", "deferred", "deferred", "downloaded"])
            self.assertEqual(download.call_count, 2)
            fetch_direct.assert_called_once()
            payload = json.loads(
                (package_dir / "metadata" / "download_results.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["summary"]["assets_deferred"], 2)
            self.assertTrue(payload["download_policy"]["instagram_circuit_opened"])
            self.assertTrue(zip_path.is_file())

    @patch("core.engine.store_media_files", return_value=[])
    @patch("core.engine.download_instagram_with_retries")
    @patch("core.engine.time.sleep")
    @patch("core.engine.time.monotonic", side_effect=[0.0, 5.0, 20.0])
    def test_publication_cooldown_is_measured_after_previous_job_finishes(
        self, monotonic, sleep, download, store
    ):
        download.return_value = ([Path("placeholder.jpg")], [
            {"attempt": 1, "outcome": "downloaded"}
        ])
        assets = [
            {
                "asset_id": f"instagram-{i}",
                "source_type": "instagram",
                "resolved_post_url": f"https://www.instagram.com/p/ABC{i}/",
            }
            for i in (1, 2)
        ]
        with tempfile.TemporaryDirectory() as temp:
            engine.run_job(
                {"schema_version": "1.0", "job_id": "test-cooldown"},
                assets,
                Path(temp),
                50 * 1024 * 1024,
                instagram_auth={"mode": "firefox"},
                download_policy={"instagram_delay_seconds": 12, "max_retries": 0},
            )
        sleep.assert_called_once_with(7.0)
        self.assertEqual(download.call_count, 2)

    def test_report_separates_assets_from_files_and_mentions_deferred(self):
        report = engine.build_report("job", [
            {
                "asset_id": "ig-1", "source_type": "instagram", "status": "downloaded",
                "files": [{"status": "downloaded"}, {"status": "downloaded"}],
                "message": "OK", "diagnostics": [],
            },
            {
                "asset_id": "ig-2", "source_type": "instagram", "status": "deferred",
                "files": [], "message": "Deferred", "diagnostics": [],
            },
        ])
        self.assertIn("Assets downloaded: 1", report)
        self.assertIn("Files saved: 2", report)
        self.assertIn("Deferred assets: 1", report)


if __name__ == "__main__":
    unittest.main()
