import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class QueueCleanupTests(unittest.TestCase):
    def test_removes_downloaded_and_duplicate_assets_only(self):
        queue = [
            {"asset_id": "ig-001"},
            {"asset_id": "ig-002"},
            {"asset_id": "ig-003"},
            {"asset_id": "url-001"},
        ]
        statuses = {
            "ig-001": "Queued",
            "ig-002": "Queued",
            "ig-003": "Queued",
            "url-001": "Queued",
        }
        results = [
            {"asset_id": "ig-001", "status": "downloaded"},
            {"asset_id": "ig-002", "status": "duplicate"},
            {"asset_id": "ig-003", "status": "failed"},
            {"asset_id": "url-001", "status": "deferred"},
        ]

        remaining, remaining_status, removed = main.prune_successful_queue_items(
            queue, statuses, results
        )

        self.assertEqual([item["asset_id"] for item in remaining], ["ig-003", "url-001"])
        self.assertEqual(remaining_status, {"ig-003": "Queued", "url-001": "Queued"})
        self.assertEqual(removed, {"ig-001", "ig-002"})

    def test_no_successful_results_leave_queue_unchanged(self):
        queue = [{"asset_id": "ig-001"}]
        statuses = {"ig-001": "Failed"}
        remaining, remaining_status, removed = main.prune_successful_queue_items(
            queue, statuses,
            [{"asset_id": "ig-001", "status": "failed"}]
        )
        self.assertEqual(remaining, queue)
        self.assertEqual(remaining_status, statuses)
        self.assertEqual(removed, set())

    def test_progress_callback_removes_successful_asset_immediately(self):
        from unittest.mock import Mock

        studio = main.AssetStudio.__new__(main.AssetStudio)
        studio.queue = [{"asset_id": "ig-001"}, {"asset_id": "ig-002"}]
        studio.queue_status = {"ig-001": "Queued", "ig-002": "Queued"}
        studio.progress_var = Mock()
        studio.status_var = Mock()
        studio.refresh_queue = Mock()

        studio.on_asset_progress(
            1, 2, {"asset_id": "ig-001", "status": "downloaded"}
        )

        self.assertEqual([item["asset_id"] for item in studio.queue], ["ig-002"])
        self.assertNotIn("ig-001", studio.queue_status)
        studio.refresh_queue.assert_called_once()
        studio.progress_var.set.assert_called_once_with(50.0)

    def test_manifest_import_is_blocked_during_active_download(self):
        from unittest.mock import Mock, patch

        studio = main.AssetStudio.__new__(main.AssetStudio)
        studio.busy = True
        studio.manifest_path_var = Mock()
        with patch("main.messagebox.showwarning") as warning:
            studio.import_manifest()
        warning.assert_called_once()
        studio.manifest_path_var.get.assert_not_called()


    def test_new_asset_ids_reserve_items_currently_being_downloaded(self):
        studio = main.AssetStudio.__new__(main.AssetStudio)
        studio.queue = [{"asset_id": "instagram-002"}]
        studio.active_download_ids = {"instagram-001"}
        self.assertEqual(studio.new_asset_id("instagram"), "instagram-003")


    def test_cleanup_does_not_remove_new_items_added_during_download(self):
        queue = [{"asset_id": "ig-001"}, {"asset_id": "direct-001"}]
        statuses = {"ig-001": "Queued", "direct-001": "Queued"}
        remaining, remaining_status, removed = main.prune_successful_queue_items(
            queue, statuses,
            [{"asset_id": "ig-001", "status": "downloaded"}]
        )
        self.assertEqual([item["asset_id"] for item in remaining], ["direct-001"])
        self.assertEqual(remaining_status, {"direct-001": "Queued"})
        self.assertEqual(removed, {"ig-001"})


class OutputDirectoryTests(unittest.TestCase):
    def test_frozen_app_uses_executable_directory(self):
        fake_exe = Path("/tmp/GTL AI Asset Downloader/Asset Downloader.exe").resolve()
        with patch.object(main.sys, "frozen", True, create=True), \
             patch.object(main.sys, "executable", str(fake_exe)):
            self.assertEqual(main.application_dir(), fake_exe.parent)
            self.assertEqual(main.default_output_dir(), fake_exe.parent / "output")

    def test_source_app_uses_main_py_directory(self):
        with patch.object(main.sys, "frozen", False, create=True):
            self.assertEqual(main.application_dir(), Path(main.__file__).resolve().parent)


if __name__ == "__main__":
    unittest.main()
