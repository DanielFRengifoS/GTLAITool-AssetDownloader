"""Render isolated desktop previews without touching the user's settings/history.

Run from the repository root: python scripts/preview_ui.py
Previews are saved in output/previews/. Requires a desktop and Pillow 11.2+ on Windows.
"""
import sys
import os
import ctypes
import tempfile
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


def capture(root, destination):
    root.update()
    root.after(200, root.quit)
    root.mainloop()
    if os.name == "nt":
        # Capture this HWND directly, including when another application is in
        # front. Desktop crops can capture unrelated windows under DPI scaling.
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        ImageGrab.grab(window=hwnd).save(destination)
        return
    x, y = root.winfo_rootx(), root.winfo_rooty()
    bounds = (x, y, x + root.winfo_width(), y + root.winfo_height())
    ImageGrab.grab(bounds).save(destination)


def render():
    destination = Path("output/previews")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary, \
         patch("main.app_data_dir", return_value=Path(temporary)), \
         patch("main.discover_firefox_profiles", return_value=[]):
        root = tk.Tk()
        studio = main.AssetStudio(root)
        studio.output_var.set("C:/Collections/GTL AI")
        try:
            capture(root, destination / "workspace-empty.png")
            studio.queue = [
                {"asset_id": "collection-001", "source_type": "instagram", "post_url": "https://www.instagram.com/p/CREATIVE01/", "media_index": 2},
                {"asset_id": "collection-002", "source_type": "direct_url", "media_url": "https://images.example.com/studio-reference.jpg"},
                {"asset_id": "collection-003", "source_type": "instagram", "post_url": "https://www.instagram.com/reel/CREATIVE02/"},
                {"asset_id": "collection-004", "source_type": "direct_url", "media_url": "https://media.example.com/landscape.mp4"},
            ]
            studio.queue_status = {"collection-001": "Queued", "collection-002": "Queued",
                                   "collection-003": "Deferred", "collection-004": "Failed"}
            studio.refresh_queue()
            capture(root, destination / "workspace-queue.png")
            studio.switch_mode("Instagram Profile")
            capture(root, destination / "profile-extractor.png")
            root.geometry("1120x760")
            capture(root, destination / "compact-workspace.png")
            studio.navigate("Settings")
            capture(root, destination / "settings.png")
            studio.navigate("About")
            capture(root, destination / "about.png")
        finally:
            studio._closing = True
            root.after_cancel(studio._event_timer)
            root.destroy()
    print(f"Saved desktop previews to {destination.resolve()}")


if __name__ == "__main__":
    render()
