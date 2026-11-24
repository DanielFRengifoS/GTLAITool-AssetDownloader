import unittest
from core import engine


class AssetManifestTests(unittest.TestCase):
    def test_direct_url_manifest_validates(self):
        data = {
            "schema_version": "1.0",
            "assets": [{
                "asset_id": "direct-001",
                "source_type": "direct_url",
                "media_url": "https://cdn.example.com/image.jpg",
            }],
        }
        self.assertEqual(len(engine.validate_manifest(data)), 1)

    def test_instagram_post_url_validates(self):
        data = {
            "schema_version": "1.0",
            "assets": [{
                "asset_id": "instagram-001",
                "source_type": "instagram",
                "post_url": "https://www.instagram.com/p/AbCd_123/",
            }],
        }
        normalized = engine.validate_manifest(data)
        self.assertEqual(normalized[0]["resolved_post_url"], data["assets"][0]["post_url"])

    def test_profile_only_is_rejected(self):
        data = {
            "schema_version": "1.0",
            "assets": [{
                "asset_id": "instagram-001",
                "source_type": "instagram",
                "profile_url": "https://www.instagram.com/example_artist/",
            }],
        }
        with self.assertRaises(engine.AssetError):
            engine.validate_manifest(data)

    def test_duplicate_asset_ids_rejected(self):
        item = {
            "asset_id": "same-001",
            "source_type": "direct_url",
            "media_url": "https://cdn.example.com/image.jpg",
        }
        with self.assertRaises(engine.AssetError):
            engine.validate_manifest({"schema_version": "1.0", "assets": [item, dict(item)]})


if __name__ == "__main__":
    unittest.main()
