"""Native Tk workflow regressions; skipped when a desktop/Tcl is unavailable."""
import json
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class DesktopWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            root = tk.Tk()
            root.destroy()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Native Tk is unavailable: {exc}")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.data_patch = patch("main.app_data_dir", return_value=self.directory)
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        self.profiles_patch = patch("main.discover_firefox_profiles", return_value=[])
        self.profiles_patch.start()
        self.addCleanup(self.profiles_patch.stop)
        self.dialog_patch = patch.multiple("main.messagebox", showinfo=lambda *a, **k: None,
                                           showwarning=lambda *a, **k: None,
                                           askyesno=lambda *a, **k: False)
        self.dialog_patch.start()
        self.addCleanup(self.dialog_patch.stop)
        self.root = tk.Tk()
        self.addCleanup(self.destroy_root)
        self.studio = main.AssetStudio(self.root)
        self.studio.output_var.set(str(self.directory / "output"))
        self.root.update()

    def destroy_root(self):
        self.studio._closing = True
        self.root.after_cancel(self.studio._event_timer)
        self.root.destroy()

    def add_asset(self, asset_id="direct-001"):
        asset = {"asset_id": asset_id, "source_type": "direct_url", "media_url": "https://example.com/image.png"}
        self.studio.queue.append(asset)
        self.studio.queue_status[asset_id] = "Queued"
        self.studio.refresh_queue()
        return asset

    def test_export_before_any_profile_scan(self):
        self.add_asset()
        destination = self.directory / "manifest.json"
        with patch("main.filedialog.asksaveasfilename", return_value=str(destination)):
            self.studio.export_manifest()
        self.assertEqual(json.loads(destination.read_text())["assets"][0]["asset_id"], "direct-001")

    def test_first_download_runs_without_profile_scan_and_saves_history(self):
        self.add_asset()
        result = {"asset_id": "direct-001", "status": "downloaded", "files": [{"status": "downloaded"}]}
        with patch("main.engine.run_job", return_value=(self.directory, self.directory / "job.zip", [result])) as job, \
             patch("main.messagebox.showerror") as error:
            self.studio.start_downloads()
            deadline = time.monotonic() + 3
            while self.studio.busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
            self.assertFalse(self.studio.busy)
            error.assert_not_called()
            job.assert_called_once()
        self.assertEqual(self.studio.queue, [])
        self.assertEqual(self.studio.load_history()[0]["files_saved"], 1)

    def test_source_forms_survive_navigation_and_mode_switch(self):
        self.studio.switch_mode("Direct URLs")
        self.studio.direct_text.insert("1.0", "https://example.com/unfinished.png")
        self.studio.switch_mode("Instagram Profile")
        profile_tree = self.studio.profile_tree
        profile_tree.insert("", "end", iid="ABC", values=("POST", "ABC", "", 1, "Caption"))
        self.studio.navigate("Settings")
        self.studio.navigate("History")
        self.studio.navigate("Downloader")
        self.assertIs(self.studio.profile_tree, profile_tree)
        self.assertTrue(profile_tree.exists("ABC"))
        self.studio.switch_mode("Direct URLs")
        self.assertEqual(self.studio.direct_text.get("1.0", "end").strip(), "https://example.com/unfinished.png")

    def test_profile_scan_can_finish_on_another_page(self):
        self.studio.switch_mode("Instagram Profile")
        self.studio.profile_url_var.set("https://www.instagram.com/example/")
        with patch("main.instagram_profile.discover_profile", return_value={"username": "example", "publications": []}):
            self.studio.scan_profile()
            self.studio.navigate("History")
            deadline = time.monotonic() + 3
            while not self.studio.profile_discovery_data and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
        self.assertEqual(self.studio.profile_discovery_data["username"], "example")
        self.studio.navigate("Downloader")
        self.assertEqual(str(self.studio.profile_scan_button.cget("state")), "normal")

    def test_retry_preserves_unattempted_queue_items(self):
        failed = self.add_asset("failed-001")
        queued = self.add_asset("queued-001")
        self.studio.queue_status["failed-001"] = "Failed"
        with patch("main.messagebox.askyesno", return_value=True), \
             patch.object(self.studio, "start_downloads") as start:
            self.studio.retry_failed_only()
        self.assertEqual(self.studio.queue, [failed, queued])
        start.assert_called_once_with(assets_to_download=[failed])

    def test_profile_controls_and_queue_fit_small_window(self):
        self.root.geometry("1120x760")
        self.studio.switch_mode("Instagram Profile")
        self.root.update()
        for widget in (self.studio.start_button, self.studio.export_button, self.studio.output_entry):
            self.assertGreater(widget.winfo_width(), 20)
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())
        self.assertGreater(self.studio.tree.winfo_height(), 80)
        self.assertGreater(self.studio.source_canvas.yview()[1], 0)

    def test_saving_without_visiting_settings_keeps_firefox_profile(self):
        profile = {"name": "Work", "path": str(self.directory / "firefox"), "is_default": True}
        self.studio.settings["firefox_profile"] = profile["path"]
        with patch("main.discover_firefox_profiles", return_value=[profile]):
            self.studio.refresh_firefox_profile_choices()
        self.studio.save_settings()
        saved = json.loads(self.studio.settings_path.read_text())
        self.assertEqual(saved["firefox_profile"], profile["path"])

    def test_queue_selection_survives_refresh(self):
        self.add_asset("direct-001")
        self.studio.tree.selection_set("direct-001")
        self.add_asset("direct-002")
        self.assertEqual(self.studio.tree.selection(), ("direct-001",))


if __name__ == "__main__":
    unittest.main()
