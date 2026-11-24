import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from PIL import Image
import io

from core.instagram_profile import (
    _normalize_records,
    _thumbnail_url_from_record,
    fetch_profile_thumbnail,
)


class ProfileThumbnailTests(unittest.TestCase):
    def test_redirect_is_validated_before_an_untrusted_request_is_sent(self):
        requests = []
        def respond(request):
            requests.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private.jpg"})
        with tempfile.TemporaryDirectory() as temporary:
            client = httpx.Client(transport=httpx.MockTransport(respond))
            with patch("core.instagram_profile.httpx.Client", return_value=client):
                result = fetch_profile_thumbnail("https://scontent.cdninstagram.com/image.jpg", Path(temporary), "REDIRECT")
        self.assertIsNone(result)
        self.assertEqual(requests, ["https://scontent.cdninstagram.com/image.jpg"])

    def test_each_intermediate_redirect_is_checked(self):
        requests = []
        def respond(request):
            requests.append(str(request.url))
            location = "https://scontent.cdninstagram.com/second.jpg" if len(requests) == 1 else "https://evil.example/private.jpg"
            return httpx.Response(302, headers={"location": location})
        with tempfile.TemporaryDirectory() as temporary:
            client = httpx.Client(transport=httpx.MockTransport(respond))
            with patch("core.instagram_profile.httpx.Client", return_value=client):
                result = fetch_profile_thumbnail("https://scontent.cdninstagram.com/image.jpg", Path(temporary), "REDIRECT")
        self.assertIsNone(result)
        self.assertEqual(len(requests), 2)
        self.assertNotIn("evil.example", " ".join(requests))

    def test_valid_relative_redirect_can_load_thumbnail(self):
        payload = io.BytesIO()
        Image.new("RGB", (20, 20), "teal").save(payload, format="PNG")
        def respond(request):
            if request.url.path == "/image.jpg":
                return httpx.Response(302, headers={"location": "/final.png"})
            return httpx.Response(200, headers={"content-type": "image/png"}, content=payload.getvalue())
        with tempfile.TemporaryDirectory() as temporary:
            client = httpx.Client(transport=httpx.MockTransport(respond))
            with patch("core.instagram_profile.httpx.Client", return_value=client):
                result = fetch_profile_thumbnail("https://scontent.cdninstagram.com/image.jpg", Path(temporary), "REDIRECT")
            self.assertTrue(result.is_file())

    def test_extracts_direct_thumbnail_url(self):
        url = "https://scontent.cdninstagram.com/v/t51.2885-15/thumb.jpg"
        self.assertEqual(
            _thumbnail_url_from_record({"shortcode": "ABC", "thumbnail_url": url}),
            url,
        )

    def test_selects_largest_nested_candidate_under_preview_limit(self):
        small = "https://scontent.cdninstagram.com/small.jpg"
        medium = "https://scontent.cdninstagram.com/medium.jpg"
        large = "https://scontent.cdninstagram.com/large.jpg"
        raw = {
            "shortcode": "ABC",
            "image_versions2": {
                "candidates": [
                    {"width": 150, "height": 150, "url": small},
                    {"width": 480, "height": 480, "url": medium},
                    {"width": 1080, "height": 1080, "url": large},
                ]
            },
        }
        self.assertEqual(_thumbnail_url_from_record(raw), medium)

    def test_prefers_thumbnail_candidate_over_full_display_url(self):
        thumb = "https://scontent.cdninstagram.com/thumb-480.jpg"
        full = "https://scontent.cdninstagram.com/display-full.jpg"
        raw = {
            "shortcode": "ABC",
            "display_url": full,
            "thumbnail_resources": [
                {"config_width": 150, "src": "https://scontent.cdninstagram.com/thumb-150.jpg"},
                {"config_width": 480, "src": thumb},
            ],
        }
        self.assertEqual(_thumbnail_url_from_record(raw), thumb)

    def test_normalizer_preserves_preview_url_for_ui(self):
        url = "https://scontent.cdninstagram.com/thumb.jpg"
        rows = [{
            "shortcode": "ABC",
            "post_date": 1795000000,
            "post_thumbnail": url,
        }]
        result = _normalize_records(rows, "example", "posts")
        self.assertEqual(result[0]["preview_url"], url)

    def test_never_uses_full_media_url_as_thumbnail_fallback(self):
        url = "https://scontent.cdninstagram.com/full-resolution.jpg"
        self.assertIsNone(_thumbnail_url_from_record({"shortcode": "ABC", "url": url}))

    def test_rejects_untrusted_or_non_https_thumbnail_hosts(self):
        self.assertIsNone(_thumbnail_url_from_record({
            "shortcode": "ABC", "thumbnail_url": "https://example.com/image.jpg"
        }))
        self.assertIsNone(_thumbnail_url_from_record({
            "shortcode": "ABC", "thumbnail_url": "http://scontent.cdninstagram.com/image.jpg"
        }))

    def test_fetches_resizes_and_caches_thumbnail(self):
        output = io.BytesIO()
        Image.new("RGB", (1200, 900), "purple").save(output, format="JPEG")
        payload = output.getvalue()
        url = "https://scontent.cdninstagram.com/image.jpg"

        response = Mock()
        response.url = httpx.URL(url)
        response.headers = {"content-type": "image/jpeg", "content-length": str(len(payload))}
        response.raise_for_status = Mock()
        response.iter_bytes.return_value = [payload]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        client = Mock()
        client.stream.return_value = response
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)

        with tempfile.TemporaryDirectory() as temp:
            with patch("core.instagram_profile.httpx.Client", return_value=client) as client_factory:
                cached = fetch_profile_thumbnail(url, Path(temp), "ABC123")
                self.assertIsNotNone(cached)
                self.assertTrue(Path(cached).is_file())
                with Image.open(cached) as image:
                    self.assertLessEqual(max(image.size), 640)
                # Same shortcode is served from the local cache on later scans.
                cached_again = fetch_profile_thumbnail(url, Path(temp), "ABC123")
                cached_without_fresh_url = fetch_profile_thumbnail(None, Path(temp), "ABC123")
                self.assertEqual(cached_again, cached)
                self.assertEqual(cached_without_fresh_url, cached)
                client_factory.assert_called_once()

    def test_rejects_redirect_to_untrusted_host(self):
        url = "https://scontent.cdninstagram.com/image.jpg"
        response = Mock()
        response.url = httpx.URL("https://evil.example/image.jpg")
        response.headers = {"content-type": "image/jpeg"}
        response.raise_for_status = Mock()
        response.iter_bytes.return_value = [b"not-used"]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        client = Mock()
        client.stream.return_value = response
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as temp:
            with patch("core.instagram_profile.httpx.Client", return_value=client):
                result = fetch_profile_thumbnail(url, Path(temp), "REDIRECT1")
        self.assertIsNone(result)

    def test_does_not_request_untrusted_thumbnail_url(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("core.instagram_profile.httpx.Client") as client_factory:
                result = fetch_profile_thumbnail(
                    "https://example.com/image.jpg", Path(temp), "ABC123"
                )
                self.assertIsNone(result)
                client_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
