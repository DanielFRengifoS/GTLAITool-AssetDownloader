from __future__ import annotations

import json
import os
import queue as event_queue
import runpy
import sys
import threading
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image, ImageDraw, ImageTk

from core import engine
from core import instagram_profile
from core.firefox_profiles import discover_firefox_profiles
from core.preferences import read_json, write_json
from core import ui
from core.workspace import WorkspaceUI


APP_NAME = "GTL AI Asset Downloader"
APP_VERSION = engine.VERSION

BG, SIDEBAR, PANEL, PANEL_2 = ui.BG, ui.SIDEBAR, ui.PANEL, ui.PANEL_2
BORDER, TEXT, MUTED = ui.BORDER, ui.TEXT, ui.MUTED
PURPLE, PURPLE_HOVER = ui.ACCENT, ui.ACCENT_HOVER
GREEN, RED, BLUE = ui.GREEN, ui.RED, ui.BLUE


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def app_data_dir() -> Path:
    if os.name == "nt" and os.getenv("APPDATA"):
        base = Path(os.environ["APPDATA"])
    else:
        base = Path.home() / ".config"
    folder = base / "GTL AI" / "Asset Downloader"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def application_dir() -> Path:
    """Return the folder containing the running EXE, or main.py in source mode."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_output_dir() -> Path:
    return application_dir() / "output"


SUCCESSFUL_QUEUE_STATUSES = {"downloaded", "duplicate"}


def prune_successful_queue_items(queue: list[dict], queue_status: dict,
                                 results: list[dict]):
    """Remove only completed assets; leave failed/deferred/queued items untouched."""
    completed_ids = {
        result.get("asset_id")
        for result in results
        if result.get("asset_id") and result.get("status") in SUCCESSFUL_QUEUE_STATUSES
    }
    remaining_queue = [
        asset for asset in queue if asset.get("asset_id") not in completed_ids
    ]
    remaining_status = {
        asset_id: status for asset_id, status in queue_status.items()
        if asset_id not in completed_ids
    }
    return remaining_queue, remaining_status, completed_ids


def clean_manifest_asset(asset: dict) -> dict:
    return {k: v for k, v in asset.items() if k != "resolved_post_url"}


class AssetStudio(WorkspaceUI):
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1380x940")
        self.root.minsize(1120, 760)
        self.root.configure(bg=BG)
        self.root.option_add("*Font", ("Segoe UI", 10))

        self.data_dir = app_data_dir()
        self.settings_path = self.data_dir / "settings.json"
        self.history_path = self.data_dir / "history.json"
        self.settings = self.load_settings()
        self.queue: list[dict] = []
        self.queue_status: dict[str, str] = {}
        self.active_download_ids: set[str] = set()
        self.manifest_job_id = ""
        self.busy = False
        self.current_page = "Downloader"
        self.input_mode = tk.StringVar(value="Instagram")

        self.default_output_path = default_output_dir()
        stored_output = str(self.settings.get("output_dir") or "").strip()
        legacy_default = Path.home() / "Documents" / "GTL AI Assets"
        stored_is_legacy_default = False
        stored_is_app_default = False
        if stored_output:
            try:
                stored_path = Path(stored_output).expanduser().resolve(strict=False)
                stored_is_legacy_default = stored_path == legacy_default.resolve(strict=False)
                stored_is_app_default = stored_path == self.default_output_path.resolve(strict=False)
            except OSError:
                stored_is_legacy_default = False
                stored_is_app_default = False

        if stored_output and (
            self.settings.get("output_dir_user_set") is True
            or not (stored_is_legacy_default or stored_is_app_default)
        ):
            # Preserve explicit custom paths, including paths saved by older builds.
            initial_output = stored_output
            self.output_dir_custom = True
        else:
            # Migrate the old Documents default to a local ./output folder.
            initial_output = str(self.default_output_path)
            self.output_dir_custom = False

        self.output_var = tk.StringVar(value=initial_output)
        self.max_mb_var = tk.StringVar(value=str(self.settings.get("max_mb", 100)))
        self.instagram_delay_var = tk.StringVar(
            value=str(self.settings.get("instagram_delay_seconds", 12))
        )
        self.instagram_auth_mode_var = tk.StringVar(
            value=self.settings.get("instagram_auth_mode", "firefox")
        )
        self.firefox_profile_var = tk.StringVar(value="")
        self.firefox_profiles = []
        self.firefox_profile_lookup = {}
        self.refresh_firefox_profile_choices()
        self.instagram_url_var = tk.StringVar()
        self.profile_url_var = tk.StringVar()
        self.profile_limit_var = tk.StringVar(value="50")
        self.profile_results = []
        self.profile_discovery_data = {}
        self.profile_scan_generation = 0
        self.profile_thumbnail_cache_dir = self.data_dir / "thumbnail_cache"
        self.profile_thumbnail_images = {}
        self.profile_placeholder_images = {}
        self.profile_preview_paths = {}
        self.profile_thumbnail_total = 0
        self.profile_thumbnails_completed = 0
        self.profile_thumbnails_available = 0
        self.profile_progress_bar = None
        self.profile_progress_running = False
        self.profile_summary_base = ""
        self.profile_preview_window = None
        self.profile_preview_shortcode = None
        self.profile_preview_photo = None
        self.media_index_var = tk.StringVar()
        self.publication_type_var = tk.StringVar(value="post")
        self.manifest_path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready — add assets to your download queue.")
        self.progress_var = tk.DoubleVar(value=0)
        self._events = event_queue.SimpleQueue()
        self._closing = False
        self._ui_icons = {}
        self.downloader_content = None
        self.mode_panels = {}
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.configure_styles()
        self.set_window_icon()
        self.build_shell()
        self.show_downloader()
        self._event_timer = self.root.after(50, self.poll_events)

    def load_settings(self):
        return read_json(self.settings_path, dict, {})

    def dispatch(self, callback, *args):
        """Workers enqueue plain data; only the Tk thread touches widgets."""
        if not self._closing:
            self._events.put((callback, args))

    def poll_events(self):
        for _ in range(100):
            try:
                callback, args = self._events.get_nowait()
            except event_queue.Empty:
                break
            try:
                callback(*args)
            except Exception:
                self.root.report_callback_exception(*sys.exc_info())
        if not self._closing:
            self._event_timer = self.root.after(50, self.poll_events)

    def close(self):
        if self.busy and not messagebox.askyesno(
            "Downloads are running", "Closing now interrupts the current download and its ZIP package may be incomplete. Close anyway?"
        ):
            return
        if not self.save_settings():
            return
        self._closing = True
        self.profile_scan_generation += 1
        self.root.after_cancel(self._event_timer)
        self.root.destroy()

    def save_settings(self):
        self.settings["output_dir"] = self.output_var.get().strip()
        self.settings["output_dir_user_set"] = bool(self.output_dir_custom)
        self.settings["instagram_auth_mode"] = self.instagram_auth_mode_var.get()
        selected_label = self.firefox_profile_var.get()
        self.settings["firefox_profile"] = self.firefox_profile_lookup.get(selected_label, "")
        try:
            delay_seconds = max(5, min(60, int(self.instagram_delay_var.get())))
        except ValueError:
            delay_seconds = 12
        self.settings["instagram_delay_seconds"] = delay_seconds
        self.instagram_delay_var.set(str(delay_seconds))
        try:
            self.settings["max_mb"] = max(1, min(2048, int(self.max_mb_var.get())))
        except ValueError:
            self.settings["max_mb"] = 100
        self.max_mb_var.set(str(self.settings["max_mb"]))
        try:
            write_json(self.settings_path, self.settings)
        except OSError as exc:
            messagebox.showerror("Could not save settings", str(exc))
            return False
        return True

    def refresh_firefox_profile_choices(self):
        """Refresh profile names without reading or exposing cookie values."""
        self.firefox_profiles = discover_firefox_profiles()
        self.firefox_profile_lookup = {"Auto-detect Firefox default profile": ""}
        for profile in self.firefox_profiles:
            suffix = " (Default)" if profile.get("is_default") else ""
            label = f"{profile['name']}{suffix}"
            # Avoid duplicate labels while retaining a readable profile name.
            if label in self.firefox_profile_lookup:
                label = f"{label} — {Path(profile['path']).name}"
            self.firefox_profile_lookup[label] = profile["path"]

        choices = list(self.firefox_profile_lookup.keys())
        saved_path = str(self.settings.get("firefox_profile") or "")
        selected = next(
            (label for label, path in self.firefox_profile_lookup.items() if path == saved_path),
            None
        )
        if selected is None and saved_path:
            selected = f"Saved profile — {Path(saved_path).name} (unavailable)"
            self.firefox_profile_lookup[selected] = saved_path
            choices.append(selected)
        if selected is None:
            selected = next(
                (label for label, path in self.firefox_profile_lookup.items()
                 if path and any(p["path"] == path and p.get("is_default") for p in self.firefox_profiles)),
                choices[0]
            )
        self.firefox_profile_var.set(selected)
        return choices

    def get_instagram_auth_settings(self):
        mode = self.instagram_auth_mode_var.get().strip().lower()
        label = self.firefox_profile_var.get()
        profile_path = self.firefox_profile_lookup.get(label, "")
        return {"mode": mode or "anonymous", "profile": profile_path}

    def open_instagram_in_firefox(self):
        webbrowser.open("https://www.instagram.com/")

    def set_window_icon(self):
        try:
            self.root.iconbitmap(str(resource_path("assets/GTL_AI_Asset_Downloader.ico")))
        except Exception:
            pass
        try:
            from PIL import Image, ImageTk
            image = Image.open(resource_path("assets/GTL_AI_Asset_Downloader.png"))
            self._icon_photo = ImageTk.PhotoImage(image.resize((64, 64)))
            self.root.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def configure_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Treeview",
            background=PANEL,
            foreground=TEXT,
            fieldbackground=PANEL,
            rowheight=46,
            borderwidth=0,
            bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Profile.Treeview",
            background=PANEL,
            foreground=TEXT,
            fieldbackground=PANEL,
            rowheight=68,
            borderwidth=0,
            bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Profile.Treeview.Heading",
            background=PANEL_2,
            foreground=MUTED,
            relief="flat",
            font=("Segoe UI Semibold", 9),
        )
        style.map("Profile.Treeview", background=[("selected", ui.ACCENT_DARK)], foreground=[("selected", TEXT)])
        style.configure(
            "Treeview.Heading",
            background=PANEL_2,
            foreground=MUTED,
            relief="flat",
            font=("Segoe UI Semibold", 9),
        )
        style.map("Treeview", background=[("selected", ui.ACCENT_DARK)], foreground=[("selected", TEXT)])
        style.configure("Treeview.Heading", padding=(10, 12), borderwidth=0)
        style.map("Treeview.Heading", background=[("active", BORDER)])
        style.configure("TScrollbar", background=PANEL_2, troughcolor=PANEL,
                        borderwidth=0, arrowsize=12, arrowcolor=MUTED,
                        bordercolor=PANEL, lightcolor=PANEL_2, darkcolor=PANEL_2)
        style.map("TScrollbar", background=[("active", BORDER)])
        style.configure("TCombobox", fieldbackground=PANEL_2, background=PANEL_2,
                        foreground=TEXT, arrowcolor=MUTED, padding=8,
                        bordercolor=BORDER, lightcolor=PANEL_2, darkcolor=PANEL_2)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL_2)],
                  foreground=[("readonly", TEXT)], selectbackground=[("readonly", PANEL_2)],
                  selectforeground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL_2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ui.ACCENT_DARK)
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=PANEL_2,
            background=PURPLE,
            bordercolor=PANEL_2,
            lightcolor=PURPLE,
            darkcolor=PURPLE,
            thickness=5,
        )

    def build_shell(self):
        self.shell = tk.Frame(self.root, bg=BG)
        self.shell.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(self.shell, bg=SIDEBAR, width=218)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        brand = tk.Frame(self.sidebar, bg=SIDEBAR)
        brand.pack(fill="x", padx=20, pady=(30, 8))
        try:
            from PIL import Image, ImageTk
            image = Image.open(resource_path("assets/GTL_AI_Asset_Downloader.png")).resize((48, 48), Image.Resampling.LANCZOS)
            self.brand_icon = ImageTk.PhotoImage(image)
            tk.Label(brand, image=self.brand_icon, bg=SIDEBAR).pack(side="left", padx=(0, 11))
        except Exception:
            tk.Label(brand, text="↓", fg=TEXT, bg=PURPLE, font=("Segoe UI", 24, "bold"),
                     width=2, height=1).pack(side="left", padx=(0, 11))
        brand_text = tk.Frame(brand, bg=SIDEBAR)
        brand_text.pack(side="left", fill="y")
        tk.Label(brand_text, text="GTL AI", bg=SIDEBAR, fg=TEXT,
                 font=("Segoe UI Semibold", 15)).pack(anchor="w")
        tk.Label(brand_text, text="ASSET DOWNLOADER", bg=SIDEBAR, fg=PURPLE,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(3, 0))

        tk.Label(
            self.sidebar,
            text="Good sources.\nGreat collections.",
            bg=SIDEBAR, fg=MUTED, font=("Segoe UI", 9),
            wraplength=180, justify="left"
        ).pack(anchor="w", padx=22, pady=(12, 30))

        tk.Label(self.sidebar, text="WORKSPACE", bg=SIDEBAR, fg="#70879D",
                 font=("Segoe UI Semibold", 8)).pack(anchor="w", padx=24, pady=(0, 12))

        self.nav_buttons = {}
        for label in ("Downloader", "History", "Settings", "About"):
            self.nav_button(label, label)

        footer = tk.Frame(self.sidebar, bg=SIDEBAR)
        footer.pack(side="bottom", fill="x", padx=20, pady=20)
        tk.Frame(footer, bg=BORDER, height=1).pack(fill="x", pady=(0, 14))
        tk.Label(footer, text="●  YOUR LOCAL WORKSPACE", bg=SIDEBAR, fg=GREEN,
                 font=("Segoe UI Semibold", 8)).pack(anchor="w", pady=(0, 9))
        tk.Label(footer, text=f"VERSION {APP_VERSION}", bg=SIDEBAR, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(footer, text="GTL AI TOOLS", bg=SIDEBAR, fg="#6F7890",
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))

        self.page_host = tk.Frame(self.shell, bg=BG)
        self.page_host.pack(side="left", fill="both", expand=True, padx=28, pady=22)
        self.content = None
        self.root.bind_all("<MouseWheel>", self.on_mousewheel, add="+")
        self.root.bind("<Control-Return>", lambda _e: self.start_downloads())

    def get_icon(self, name, color=MUTED, size=20):
        key = (name, color, size)
        if key not in self._ui_icons:
            self._ui_icons[key] = ui.icon(name, color, size, self.root)
        return self._ui_icons[key]

    def on_mousewheel(self, event):
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if isinstance(widget, (ttk.Treeview, tk.Text)):
                return
            if isinstance(widget, tk.Canvas):
                if widget.yview() != (0.0, 1.0):
                    widget.yview_scroll(-int(event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
                return "break"
            widget = getattr(widget, "master", None)

    def nav_button(self, label, glyph):
        btn = tk.Button(
            self.sidebar, text=f"   {label}", image=self.get_icon(glyph), compound="left", anchor="w",
            command=lambda name=label: self.navigate(name),
            bg=SIDEBAR, fg=MUTED, activebackground=ui.ACCENT_DARK, activeforeground=TEXT,
            relief="flat", bd=0, padx=16, pady=13, font=("Segoe UI Semibold", 10),
            cursor="hand2"
        )
        btn.pack(fill="x", padx=14, pady=4)
        self.nav_buttons[label] = btn

    def navigate(self, page):
        self.current_page = page
        for name, button in self.nav_buttons.items():
            active = name == page
            button.configure(bg=ui.ACCENT_DARK if active else SIDEBAR,
                             fg=PURPLE if active else MUTED,
                             image=self.get_icon(name, PURPLE if active else MUTED))
        if page == "Downloader":
            self.show_downloader()
        elif page == "History":
            self.show_history()
        elif page == "Settings":
            self.show_settings()
        else:
            self.show_about()

    def clear_content(self):
        for child in self.page_host.winfo_children():
            if child is self.downloader_content:
                child.pack_forget()
            else:
                child.destroy()
        self.content = tk.Frame(self.page_host, bg=BG)
        self.content.pack(fill="both", expand=True)

    def scrollable_page(self):
        """Keep long history and settings pages accessible at any window height."""
        host = self.content
        canvas = tk.Canvas(host, bg=BG, bd=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(host, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.content = tk.Frame(canvas, bg=BG)
        window = canvas.create_window((0, 0), window=self.content, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        self.content.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))

    def heading(self, title, subtitle):
        top = tk.Frame(self.content, bg=BG)
        top.pack(fill="x", pady=(0, 18))
        tk.Label(top, text="GTL AI  /  WORKSPACE", bg=BG, fg=PURPLE,
                 font=("Segoe UI Semibold", 8)).pack(anchor="w", pady=(0, 6))
        actions = tk.Frame(top, bg=BG)
        actions.pack(side="right", anchor="center")
        if title == "Asset workspace":
            self.start_button = self.styled_button(actions, "Download assets", self.start_downloads, primary=True)
            self.start_button.configure(image=self.get_icon("download", BG, 18), compound="left")
            self.start_button.pack(side="right")
        tk.Label(top, text=title, bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 26)).pack(anchor="w")
        tk.Label(top, text=subtitle, bg=BG, fg=MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(5, 0))

    def card(self, parent, padx=18, pady=16):
        frame = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill="x", pady=(0, 14))
        inner = tk.Frame(frame, bg=PANEL)
        inner.pack(fill="both", expand=True, padx=padx, pady=pady)
        return inner

    def label(self, parent, text, color=TEXT, size=10, bold=False):
        return tk.Label(
            parent, text=text, bg=parent.cget("bg"), fg=color,
            font=("Segoe UI Semibold" if bold else "Segoe UI", size)
        )

    def styled_button(self, parent, text, command, primary=False, small=False, color=None):
        bg = color or (PURPLE if primary else PANEL_2)
        fg = BG if primary else TEXT
        btn = tk.Button(
            parent, text=text, command=command,
            bg=bg, fg=fg, activebackground=PURPLE_HOVER if primary else BORDER,
            activeforeground=fg, relief="flat", bd=0, highlightthickness=1,
            highlightbackground=bg, highlightcolor=PURPLE,
            padx=16 if not small else 11, pady=10 if not small else 7,
            font=("Segoe UI Semibold", 9 if small else 10),
            cursor="hand2", disabledforeground="#708397", takefocus=True
        )
        btn.bind("<Enter>", lambda _e: btn.configure(bg=PURPLE_HOVER if primary else BORDER) if str(btn.cget("state")) != "disabled" else None)
        btn.bind("<Leave>", lambda _e: btn.configure(bg=bg))
        return btn

    def input_entry(self, parent, variable, placeholder=None, width=None):
        entry = tk.Entry(
            parent, textvariable=variable, bg="#0E121D", fg=TEXT,
            insertbackground=TEXT, relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=PURPLE,
            font=("Segoe UI", 10), width=width
        )
        entry.pack(fill="x", ipady=10)
        return entry

    def render_input_mode(self):
        for panel in self.mode_panels.values():
            panel.pack_forget()
        mode = self.input_mode.get()
        if mode in self.mode_panels:
            self.input_inner = self.mode_panels[mode]
            self.input_inner.pack(fill="x", padx=20, pady=18)
            self.source_canvas.yview_moveto(0)
            return
        self.input_inner = tk.Frame(self.input_body, bg=PANEL)
        self.input_inner.pack(fill="x", padx=20, pady=18)
        self.mode_panels[mode] = self.input_inner
        self.source_canvas.yview_moveto(0)

        if self.input_mode.get() == "Instagram":
            tk.Label(self.input_inner, text="INSTAGRAM PUBLICATION",
                     bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w")
            tk.Label(self.input_inner, text="Paste a specific post, carousel or Reel URL. A profile URL alone is not enough.",
                     bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 12))
            row = tk.Frame(self.input_inner, bg=PANEL)
            row.pack(fill="x")
            left = tk.Frame(row, bg=PANEL)
            left.pack(side="left", fill="x", expand=True, padx=(0, 10))
            tk.Label(left, text="Post / Reel URL", bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 5))
            self.input_entry(left, self.instagram_url_var)
            right = tk.Frame(row, bg=PANEL, width=180)
            right.pack(side="left", fill="y")
            tk.Label(right, text="Carousel item (optional)", bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 5))
            self.input_entry(right, self.media_index_var)
            self.styled_button(self.input_inner, "+  Add Instagram publication", self.add_instagram, primary=True, small=True).pack(anchor="e", pady=(12, 0))

        elif self.input_mode.get() == "Instagram Profile":
            tk.Label(self.input_inner, text="INSTAGRAM PROFILE EXTRACTOR",
                     bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w")
            tk.Label(self.input_inner, text="Discover the latest public posts and Reels, review the results, then add selected publications to the download queue.",
                     bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=850, justify="left").pack(anchor="w", pady=(4, 12))
            row = tk.Frame(self.input_inner, bg=PANEL)
            row.pack(fill="x")
            url_frame = tk.Frame(row, bg=PANEL)
            url_frame.pack(side="left", fill="x", expand=True, padx=(0, 10))
            tk.Label(url_frame, text="Public profile URL", bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 5))
            self.input_entry(url_frame, self.profile_url_var)
            limit_frame = tk.Frame(row, bg=PANEL, width=130)
            limit_frame.pack(side="left", fill="y")
            tk.Label(limit_frame, text="Publication limit", bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 5))
            tk.Entry(limit_frame, textvariable=self.profile_limit_var, bg="#0E121D", fg=TEXT,
                     insertbackground=TEXT, relief="flat", highlightthickness=1,
                     highlightbackground=BORDER, highlightcolor=PURPLE, width=10).pack(fill="x", ipady=10)
            actions = tk.Frame(self.input_inner, bg=PANEL)
            actions.pack(fill="x", pady=(10, 8))
            self.profile_scan_button = self.styled_button(actions, "⌕  Scan profile", self.scan_profile, primary=True, small=True)
            self.profile_scan_button.pack(side="left")
            self.styled_button(actions, "Add selected to queue", self.add_profile_selected, small=True).pack(side="right")
            self.profile_summary_var = tk.StringVar(value="No profile scanned yet.")
            progress_info = tk.Frame(self.input_inner, bg=PANEL)
            progress_info.pack(fill="x", pady=(2, 6))
            tk.Label(
                progress_info, textvariable=self.profile_summary_var,
                bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w"
            ).pack(side="left", fill="x", expand=True)
            self.profile_progress_bar = ttk.Progressbar(
                progress_info, mode="determinate", length=150,
                style="Horizontal.TProgressbar"
            )
            self.profile_progress_bar.pack(side="right", padx=(10, 0))
            result_frame = tk.Frame(self.input_inner, bg=PANEL, height=166)
            result_frame.pack(fill="x", expand=False)
            result_frame.pack_propagate(False)
            cols = ("type", "shortcode", "date", "media", "caption")
            self.profile_tree = ttk.Treeview(
                result_frame, columns=cols, show="tree headings", height=3,
                selectmode="extended", style="Profile.Treeview"
            )
            self.profile_tree.heading("#0", text="PREVIEW")
            self.profile_tree.column("#0", width=78, minwidth=78, stretch=False, anchor="center")
            for col, title in [("type","TYPE"),("shortcode","SHORTCODE"),("date","DATE"),("media","MEDIA"),("caption","CAPTION")]:
                self.profile_tree.heading(col, text=title)
            self.profile_tree.column("type", width=75, stretch=False)
            self.profile_tree.column("shortcode", width=130, stretch=False)
            self.profile_tree.column("date", width=135, stretch=False)
            self.profile_tree.column("media", width=75, stretch=False)
            self.profile_tree.column("caption", width=360, stretch=True)
            self.create_profile_placeholders()
            self.profile_tree.bind("<Button-1>", self.on_profile_tree_click, add="+")
            self.profile_tree.bind("<Double-1>", self.on_profile_tree_double_click, add="+")
            self.profile_tree.pack(side="left", fill="both", expand=True)
            profile_scroll = ttk.Scrollbar(result_frame, orient="vertical", command=self.profile_tree.yview)
            self.profile_tree.configure(yscrollcommand=profile_scroll.set)
            profile_scroll.pack(side="right", fill="y")
            tk.Label(
                self.input_inner,
                text="Click a thumbnail to enlarge it. Previews load in the background; original media is not downloaded.",
                bg=PANEL, fg=MUTED, font=("Segoe UI", 8)
            ).pack(anchor="w", pady=(6, 0))

        elif self.input_mode.get() == "Direct URLs":
            tk.Label(self.input_inner, text="DIRECT IMAGE / VIDEO URLS",
                     bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w")
            tk.Label(self.input_inner, text="Paste one direct media URL per line. Google Images result-page URLs are not direct media URLs.",
                     bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 10))
            self.direct_text = tk.Text(
                self.input_inner, height=4, bg="#0E121D", fg=TEXT,
                insertbackground=TEXT, relief="flat", highlightthickness=1,
                highlightbackground=BORDER, highlightcolor=PURPLE,
                font=("Consolas", 9), wrap="word"
            )
            self.direct_text.pack(fill="x")
            self.styled_button(self.input_inner, "+  Add URLs to queue", self.add_direct_urls, primary=True, small=True).pack(anchor="e", pady=(10, 0))

        else:
            tk.Label(self.input_inner, text="IMPORT CANONICAL MANIFEST",
                     bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w")
            tk.Label(self.input_inner, text="Load an asset_manifest.json produced by the Scouting Agent.",
                     bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 12))
            row = tk.Frame(self.input_inner, bg=PANEL)
            row.pack(fill="x")
            tk.Entry(row, textvariable=self.manifest_path_var, bg="#0E121D", fg=TEXT,
                     insertbackground=TEXT, relief="flat", highlightthickness=1,
                     highlightbackground=BORDER, highlightcolor=PURPLE).pack(side="left", fill="x", expand=True, ipady=9)
            self.styled_button(row, "Choose JSON", self.load_manifest, small=True).pack(side="left", padx=(8, 0))
            self.styled_button(self.input_inner, "Import manifest to queue", self.import_manifest, primary=True, small=True).pack(anchor="e", pady=(12, 0))

    def add_instagram(self):
        value = self.instagram_url_var.get().strip()
        if not value:
            messagebox.showwarning("Missing publication", "Paste an Instagram post, carousel or Reel URL.")
            return
        asset = {
            "asset_id": self.new_asset_id("instagram"),
            "source_type": "instagram",
            "post_url": value,
            "media_type": "auto",
            "rights_status": "unknown",
        }
        index = self.media_index_var.get().strip()
        if index:
            try:
                asset["media_index"] = int(index)
            except ValueError:
                messagebox.showerror("Invalid carousel item", "Carousel item must be a number starting at 1.")
                return
        try:
            engine.validate_manifest({"schema_version": "1.0", "assets": [asset]})
        except engine.AssetError as exc:
            messagebox.showerror("Invalid Instagram URL", str(exc))
            return
        self.queue.append(asset)
        self.queue_status[asset["asset_id"]] = "Queued"
        self.instagram_url_var.set("")
        self.media_index_var.set("")
        self.refresh_queue()

    def start_profile_progress(self):
        bar = getattr(self, "profile_progress_bar", None)
        if bar is None:
            return
        try:
            if bar.winfo_exists():
                bar.stop()
                bar.configure(mode="indeterminate", maximum=100, value=0)
                bar.start(12)
                self.profile_progress_running = True
        except Exception:
            self.profile_progress_running = False

    def stop_profile_progress(self):
        bar = getattr(self, "profile_progress_bar", None)
        try:
            if bar is not None and bar.winfo_exists():
                bar.stop()
        except Exception:
            pass
        self.profile_progress_running = False

    def set_profile_thumbnail_progress(self, total, completed=0):
        bar = getattr(self, "profile_progress_bar", None)
        if bar is None:
            return
        self.stop_profile_progress()
        try:
            if bar.winfo_exists():
                bar.configure(
                    mode="determinate",
                    maximum=max(1, int(total)),
                    value=max(0, min(int(completed), max(1, int(total))))
                )
        except Exception:
            pass

    def scan_profile(self):
        if self.busy:
            return
        profile_url = self.profile_url_var.get().strip()
        try:
            limit = int(self.profile_limit_var.get().strip())
            engine.validate_instagram_profile_url(profile_url)
            if not 1 <= limit <= 500:
                raise engine.AssetError("Publication limit must be between 1 and 500.")
        except Exception as exc:
            messagebox.showerror("Invalid profile input", str(exc))
            return

        self.profile_scan_generation += 1
        generation = self.profile_scan_generation
        self.profile_thumbnail_images = {}
        self.profile_preview_paths = {}
        self.profile_thumbnail_total = 0
        self.profile_thumbnails_completed = 0
        self.profile_thumbnails_available = 0
        self.close_profile_preview()

        auth_settings = self.get_instagram_auth_settings()
        auth_label = "Firefox session" if auth_settings["mode"] == "firefox" else "anonymous access"
        self.profile_scan_button.configure(state="disabled", text="Scanning…")
        self.start_profile_progress()
        self.profile_summary_var.set(
            f"Discovering public posts and Reels using {auth_label}. No media is being downloaded yet."
        )
        for item in self.profile_tree.get_children():
            self.profile_tree.delete(item)

        def progress(message):
            self.dispatch(self.update_profile_scan_progress, message, generation)

        def worker():
            try:
                result = instagram_profile.discover_profile(
                    profile_url, limit=limit, progress_callback=progress,
                    auth_config=auth_settings
                )
                self.dispatch(self.on_profile_scan_finished, result, generation)
            except Exception as exc:
                self.dispatch(self.on_profile_scan_error, str(exc), generation)

        threading.Thread(target=worker, daemon=True).start()

    def update_profile_scan_progress(self, message, generation):
        if generation == self.profile_scan_generation:
            self.profile_summary_var.set(message)

    def create_profile_placeholders(self):
        """Create small local fallback thumbnails for rows without a remote preview."""
        self.profile_placeholder_images = {}
        for kind, label in (("post", "IMG"), ("reel", "▶")):
            image = Image.new("RGB", (68, 68), "#202638")
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((2, 2, 65, 65), radius=11, fill="#202638", outline="#46506B", width=2)
            if kind == "reel":
                draw.ellipse((17, 17, 51, 51), fill=PURPLE)
                draw.polygon([(31, 25), (31, 43), (44, 34)], fill="#FFFFFF")
            else:
                draw.rounded_rectangle((18, 18, 50, 50), radius=7, outline="#A5AEC2", width=3)
                draw.ellipse((25, 25, 32, 32), fill="#A5AEC2")
                draw.line((21, 45, 31, 35, 38, 42, 44, 36, 49, 45), fill="#A5AEC2", width=3)
            self.profile_placeholder_images[kind] = ImageTk.PhotoImage(image)

    def _profile_placeholder_for(self, item):
        kind = "reel" if item.get("publication_type") == "reel" else "post"
        return self.profile_placeholder_images.get(kind)

    def start_profile_thumbnail_loading(self, generation):
        """Fetch only thumbnail URLs on a small worker pool; never block Tk's UI thread."""
        # Include records without a fresh URL so a previously cached preview
        # can still be displayed if Instagram omits the thumbnail on a later scan.
        candidates = [
            (item["shortcode"], item.get("preview_url"))
            for item in self.profile_results
        ]
        self.profile_thumbnail_total = len(candidates)
        self.profile_thumbnails_completed = 0
        self.profile_thumbnails_available = 0
        self.set_profile_thumbnail_progress(len(candidates), 0)

        if not candidates:
            self.profile_summary_var.set(
                f"{self.profile_summary_base} No publications are available to preview."
            )
            return

        self.profile_summary_var.set(
            f"{self.profile_summary_base} Loading previews: 0/{len(candidates)}…"
        )

        def worker():
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="profile-thumbnails") as pool:
                futures = {
                    pool.submit(
                        instagram_profile.fetch_profile_thumbnail,
                        url, self.profile_thumbnail_cache_dir, shortcode
                    ): shortcode
                    for shortcode, url in candidates
                }
                for future in as_completed(futures):
                    shortcode = futures[future]
                    try:
                        path = future.result()
                    except Exception:
                        path = None
                    self.dispatch(
                        self.update_profile_thumbnail,
                        shortcode, str(path) if path else None, generation
                    )

        threading.Thread(target=worker, daemon=True).start()

    def update_profile_thumbnail(self, shortcode, path, generation):
        if generation != self.profile_scan_generation:
            return
        self.profile_thumbnails_completed += 1
        bar = getattr(self, "profile_progress_bar", None)
        try:
            if bar is not None and bar.winfo_exists():
                bar.configure(
                    mode="determinate",
                    maximum=max(1, self.profile_thumbnail_total),
                    value=self.profile_thumbnails_completed
                )
        except Exception:
            pass
        if path:
            try:
                with Image.open(path) as source:
                    image = source.convert("RGB")
                    image.thumbnail((64, 64), Image.Resampling.LANCZOS)
                    item = next((row for row in self.profile_results if row["shortcode"] == shortcode), None)
                    if item and item.get("publication_type") == "reel":
                        draw = ImageDraw.Draw(image)
                        draw.ellipse((image.width - 23, image.height - 23, image.width - 3, image.height - 3),
                                     fill=PURPLE, outline="#FFFFFF", width=1)
                        draw.polygon([
                            (image.width - 16, image.height - 19),
                            (image.width - 16, image.height - 7),
                            (image.width - 7, image.height - 13),
                        ], fill="#FFFFFF")
                    photo = ImageTk.PhotoImage(image)
                self.profile_thumbnail_images[shortcode] = photo
                self.profile_preview_paths[shortcode] = Path(path)
                if hasattr(self, "profile_tree") and self.profile_tree.winfo_exists() and self.profile_tree.exists(shortcode):
                    self.profile_tree.item(shortcode, image=photo)
                self.profile_thumbnails_available += 1
            except Exception:
                pass

        if self.profile_thumbnail_total:
            self.profile_summary_var.set(
                f"{self.profile_summary_base} Previews ready: "
                f"{self.profile_thumbnails_available}/{self.profile_thumbnail_total}."
            )
        if self.profile_thumbnails_completed >= self.profile_thumbnail_total:
            self.stop_profile_progress()
        if self.profile_preview_shortcode == shortcode:
            self.refresh_profile_preview_image(shortcode)

    def on_profile_scan_finished(self, result, generation=None):
        if generation is not None and generation != self.profile_scan_generation:
            return

        # Keep expiring/signed CDN URLs in transient UI state only. They are
        # deliberately excluded from package metadata and exported manifests.
        self.profile_discovery_data = dict(result)
        self.profile_results = result.get("publications", [])
        self.profile_discovery_data["publications"] = [
            {key: value for key, value in item.items() if key != "preview_url"}
            for item in self.profile_results
        ]

        for item in self.profile_tree.get_children():
            self.profile_tree.delete(item)
        for item in self.profile_results:
            caption = str(item.get("caption") or "").replace("\n", " ").strip()
            if len(caption) > 110:
                caption = caption[:107] + "..."
            date_value = item.get("date") or ""
            if not date_value and item.get("timestamp"):
                date_value = datetime.fromtimestamp(item["timestamp"]).strftime("%Y-%m-%d")
            self.profile_tree.insert(
                "", "end", iid=item["shortcode"],
                image=self._profile_placeholder_for(item),
                values=(
                    item["publication_type"].upper(),
                    item["shortcode"],
                    str(date_value)[:24],
                    item.get("media_count") or "—",
                    caption,
                )
            )
        try:
            if self.profile_scan_button.winfo_exists():
                self.profile_scan_button.configure(state="normal", text="⌕  Scan profile")
        except Exception:
            pass
        self.profile_summary_base = (
            f"Found {len(self.profile_results)} unique publications for @{result.get('username','')}."
        )
        if result.get("warnings"):
            self.profile_summary_base += " Some extractor warnings were returned; see extraction report."
        self.profile_summary_var.set(self.profile_summary_base)
        self.set_profile_thumbnail_progress(len(self.profile_results), 0)
        self.start_profile_thumbnail_loading(generation or self.profile_scan_generation)

        if not self.profile_results:
            messagebox.showwarning(
                "No publications found",
                "Instagram returned no parseable public post metadata. This can happen when Instagram requires a session, rate-limits requests, or changes its response format."
            )

    def on_profile_scan_error(self, message, generation=None):
        if generation is not None and generation != self.profile_scan_generation:
            return
        try:
            if self.profile_scan_button.winfo_exists():
                self.profile_scan_button.configure(state="normal", text="⌕  Scan profile")
        except Exception:
            pass
        self.stop_profile_progress()
        self.set_profile_thumbnail_progress(1, 0)
        self.profile_summary_var.set("Profile scan failed. No downloads were started.")
        log_path = app_data_dir() / "profile_discovery.log"
        try:
            log_path.write_text(
                f"GTL AI Asset Downloader {APP_VERSION} — Instagram profile discovery\n"
                f"Time: {datetime.now().isoformat(timespec='seconds')}\n"
                f"Profile: {self.profile_url_var.get().strip()}\n\n"
                f"{message}\n",
                encoding="utf-8"
            )
            visible_message = message[:1800] + (
                "\n\nFull diagnostic saved to:\n" + str(log_path)
            )
        except Exception:
            visible_message = message[:2200]
        messagebox.showerror("Profile discovery failed", visible_message)

    def on_profile_tree_click(self, event):
        if not hasattr(self, "profile_tree") or not self.profile_tree.winfo_exists():
            return
        row = self.profile_tree.identify_row(event.y)
        column = self.profile_tree.identify_column(event.x)
        region = self.profile_tree.identify_region(event.x, event.y)
        if row and (column == "#0" or region == "tree"):
            self.root.after(60, self.open_profile_preview, row)

    def on_profile_tree_double_click(self, event):
        if not hasattr(self, "profile_tree") or not self.profile_tree.winfo_exists():
            return
        row = self.profile_tree.identify_row(event.y)
        if row:
            self.root.after_idle(self.open_profile_preview, row)

    def open_profile_preview(self, shortcode):
        item = next((row for row in self.profile_results if row.get("shortcode") == shortcode), None)
        if not item:
            return
        if self.profile_preview_window is not None:
            try:
                if self.profile_preview_window.winfo_exists():
                    if self.profile_preview_shortcode == shortcode:
                        self.profile_preview_window.lift()
                        return
                    self.profile_preview_window.destroy()
            except Exception:
                pass

        window = tk.Toplevel(self.root)
        window.title(f"Instagram preview · {shortcode}")
        window.configure(bg=BG)
        window.geometry("760x760")
        window.minsize(580, 560)
        window.transient(self.root)
        self.profile_preview_window = window
        self.profile_preview_shortcode = shortcode
        self.profile_preview_photo = None

        header = tk.Frame(window, bg=BG)
        header.pack(fill="x", padx=22, pady=(18, 12))
        title = f"@{self.profile_discovery_data.get('username', '')}  ·  {item['publication_type'].upper()}"
        tk.Label(header, text=title, bg=BG, fg=TEXT, font=("Segoe UI Semibold", 15)).pack(side="left")
        self.styled_button(
            header, "Open on Instagram", lambda url=item["post_url"]: webbrowser.open(url), small=True
        ).pack(side="right")

        preview_frame = tk.Frame(window, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        preview_frame.pack(fill="both", expand=True, padx=22, pady=(0, 12))
        self.profile_preview_image_label = tk.Label(
            preview_frame, bg=PANEL, fg=MUTED,
            text="Loading thumbnail preview…", font=("Segoe UI", 11)
        )
        self.profile_preview_image_label.pack(expand=True, padx=16, pady=16)

        details = tk.Frame(window, bg=BG)
        details.pack(fill="x", padx=22, pady=(0, 18))
        date_value = item.get("date") or ""
        if not date_value and item.get("timestamp"):
            date_value = datetime.fromtimestamp(item["timestamp"]).strftime("%Y-%m-%d")
        tk.Label(
            details, text=f"{shortcode}  ·  {date_value or 'Date unavailable'}  ·  "
                          f"{item.get('media_count') or 1} media item(s)",
            bg=BG, fg=MUTED, font=("Segoe UI", 9)
        ).pack(anchor="w")
        caption = str(item.get("caption") or "No caption available.")
        if len(caption) > 1200:
            caption = caption[:1197] + "..."
        tk.Label(
            details, text=caption, bg=BG, fg=TEXT, wraplength=760,
            justify="left", font=("Segoe UI", 10)
        ).pack(anchor="w", pady=(7, 0))

        window.protocol("WM_DELETE_WINDOW", self.close_profile_preview)
        self.refresh_profile_preview_image(shortcode)

    def refresh_profile_preview_image(self, shortcode):
        window = self.profile_preview_window
        if window is None or self.profile_preview_shortcode != shortcode:
            return
        try:
            if not window.winfo_exists():
                return
        except Exception:
            return

        path = self.profile_preview_paths.get(shortcode)
        if path and Path(path).is_file():
            try:
                with Image.open(path) as source:
                    image = source.convert("RGB")
                    image.thumbnail((620, 540), Image.Resampling.LANCZOS)
                    self.profile_preview_photo = ImageTk.PhotoImage(image)
                self.profile_preview_image_label.configure(
                    image=self.profile_preview_photo, text=""
                )
                return
            except Exception:
                pass
        item = next((row for row in self.profile_results if row.get("shortcode") == shortcode), None)
        if item and item.get("preview_url"):
            self.profile_preview_image_label.configure(
                image="", text="Loading thumbnail preview…"
            )
        else:
            self.profile_preview_image_label.configure(
                image="", text="Instagram did not expose a preview thumbnail for this publication."
            )

    def close_profile_preview(self):
        window = getattr(self, "profile_preview_window", None)
        self.profile_preview_window = None
        self.profile_preview_shortcode = None
        self.profile_preview_photo = None
        try:
            if window is not None and window.winfo_exists():
                window.destroy()
        except Exception:
            pass

    def add_profile_selected(self):
        if not self.profile_results:
            messagebox.showinfo("No discovery results", "Scan a profile before adding publications.")
            return
        selected = set(self.profile_tree.selection())
        if not selected:
            messagebox.showinfo("Nothing selected", "Select one or more discovered publications.")
            return
        added = 0
        existing_urls = {a.get("post_url") for a in self.queue if a.get("source_type") == "instagram"}
        for item in self.profile_results:
            if item["shortcode"] not in selected or item["post_url"] in existing_urls:
                continue
            asset = {
                "asset_id": self.new_asset_id("instagram"),
                "source_type": "instagram",
                "post_url": item["post_url"],
                "profile_url": item["profile_url"],
                "shortcode": item["shortcode"],
                "publication_type": item["publication_type"],
                "media_type": "auto",
                "rights_status": "unknown",
                "discovery_metadata": {
                    "date": item.get("date"),
                    "timestamp": item.get("timestamp"),
                    "caption": item.get("caption"),
                    "media_count": item.get("media_count"),
                    "is_pinned": item.get("is_pinned", False),
                },
            }
            try:
                engine.validate_manifest({"schema_version": "1.0", "assets": [asset]})
            except engine.AssetError:
                continue
            self.queue.append(asset)
            self.queue_status[asset["asset_id"]] = "Queued"
            existing_urls.add(item["post_url"])
            added += 1
        self.refresh_queue()
        self.status_var.set(f"Added {added} discovered Instagram publication(s) to the queue.")
        if added:
            messagebox.showinfo("Publications added", f"Added {added} publication(s). The download queue is ready.")

    def add_direct_urls(self):
        raw = self.direct_text.get("1.0", "end").strip()
        urls = [line.strip() for line in raw.splitlines() if line.strip()]
        if not urls:
            messagebox.showwarning("No URLs", "Paste at least one direct image or video URL.")
            return
        added = 0
        errors = []
        rejected = []
        for url in urls:
            asset = {
                "asset_id": self.new_asset_id("direct"),
                "source_type": "direct_url",
                "media_url": url,
                "media_type": "auto",
                "rights_status": "unknown",
            }
            try:
                engine.validate_manifest({"schema_version": "1.0", "assets": [asset]})
                self.queue.append(asset)
                self.queue_status[asset["asset_id"]] = "Queued"
                added += 1
            except engine.AssetError as exc:
                errors.append(f"{url}\n  {exc}")
                rejected.append(url)
        self.direct_text.delete("1.0", "end")
        if rejected:
            self.direct_text.insert("1.0", "\n".join(rejected))
        self.refresh_queue()
        if errors:
            messagebox.showwarning("Some URLs were skipped", f"Added {added} URL(s).\n\n" + "\n\n".join(errors[:5]))
        elif added:
            self.status_var.set(f"Added {added} direct URL(s) to the queue.")

    def new_asset_id(self, prefix):
        used = {a["asset_id"] for a in self.queue} | set(self.active_download_ids)
        n = 1
        while f"{prefix}-{n:03d}" in used:
            n += 1
        return f"{prefix}-{n:03d}"

    def load_manifest(self):
        path = filedialog.askopenfilename(
            title="Select asset_manifest.json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if path:
            self.manifest_path_var.set(path)

    def import_manifest(self):
        if self.busy:
            messagebox.showwarning(
                "Download in progress",
                "Wait for the current download to finish before replacing the queue with a manifest."
            )
            return
        path = self.manifest_path_var.get().strip()
        if not path or not Path(path).is_file():
            messagebox.showwarning("Choose a manifest", "Select a valid asset_manifest.json first.")
            return
        try:
            data, assets = engine.load_manifest(Path(path))
            if not assets:
                raise engine.AssetError("The manifest has no assets.")
            self.queue = [clean_manifest_asset(a) for a in assets]
            self.queue_status = {a["asset_id"]: "Queued" for a in self.queue}
            self.manifest_job_id = data.get("job_id", "")
            self.profile_discovery_data = data.get("profile_discovery") if isinstance(data.get("profile_discovery"), dict) else {}
            self.refresh_queue()
            self.status_var.set(f"Imported {len(self.queue)} asset(s) from manifest.")
        except Exception as exc:
            messagebox.showerror("Manifest import failed", str(exc))

    def refresh_queue(self):
        if not hasattr(self, "tree"):
            return
        try:
            if not self.tree.winfo_exists():
                return
        except Exception:
            return
        selected = set(self.tree.selection())
        position = self.tree.yview()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, asset in enumerate(self.queue):
            source = "Instagram" if asset["source_type"] == "instagram" else "Direct URL"
            url = asset.get("post_url") or asset.get("media_url") or asset.get("shortcode", "")
            selection = f"Item #{asset['media_index']}" if asset.get("media_index") else "All media"
            status = self.queue_status.get(asset["asset_id"], "Queued")
            self.tree.insert("", "end", iid=asset["asset_id"], values=(source, url, selection, status),
                             tags=("odd" if index % 2 else "even", status.lower()))
        self.tree.selection_set([asset["asset_id"] for asset in self.queue if asset["asset_id"] in selected])
        if position:
            self.tree.yview_moveto(position[0])
        self.queue_title.set(f"Download queue  ·  {len(self.queue)}")
        counts = {
            "total": len(self.queue),
            "instagram": sum(a["source_type"] == "instagram" for a in self.queue),
            "direct": sum(a["source_type"] == "direct_url" for a in self.queue),
            "attention": sum(self.queue_status.get(a["asset_id"], "").lower() in {"failed", "deferred"} for a in self.queue),
        }
        for key, value in counts.items():
            self.stats[key].set(str(value))
        if self.queue:
            self.queue_empty.place_forget()
        else:
            self.queue_empty.place(relx=0.5, rely=0.58, anchor="center")
        self.start_button.configure(state="disabled" if self.busy or not self.queue else "normal",
                                    text="Downloading…" if self.busy else "Download assets")
        self.update_retry_button()

    def update_retry_button(self):
        button = getattr(self, "retry_failed_button", None)
        if button is None:
            return
        retryable = any(
            self.queue_status.get(asset["asset_id"], "").lower() in {"failed", "deferred"}
            for asset in self.queue
        )
        try:
            button.configure(state="normal" if retryable and not self.busy else "disabled")
        except Exception:
            pass

    def retry_failed_only(self):
        if self.busy:
            return
        retryable_statuses = {"failed", "deferred"}
        failed_assets = [
            asset for asset in self.queue
            if self.queue_status.get(asset["asset_id"], "").lower() in retryable_statuses
        ]
        if not failed_assets:
            messagebox.showinfo("Nothing to retry", "There are no failed or deferred assets in the current queue.")
            return
        if not messagebox.askyesno(
            "Retry failed assets only?",
            f"{len(failed_assets)} failed/deferred asset(s) will be retried. "
            "Successful assets will not be downloaded again. If Instagram rate-limited the previous job, "
            "wait a few minutes before retrying. Continue?"
        ):
            return
        for asset in failed_assets:
            self.queue_status[asset["asset_id"]] = "Queued"
        self.manifest_job_id = ""
        self.refresh_queue()
        self.start_downloads(assets_to_download=failed_assets)

    def remove_selected(self):
        if self.busy:
            messagebox.showinfo("Download in progress", "Wait for the current download to finish before removing queued items.")
            return
        if not hasattr(self, "tree"):
            return
        selected = set(self.tree.selection())
        self.queue = [a for a in self.queue if a["asset_id"] not in selected]
        for asset_id in selected:
            self.queue_status.pop(asset_id, None)
        self.refresh_queue()

    def clear_queue(self):
        if self.busy:
            messagebox.showinfo("Download in progress", "Wait for the current download to finish before clearing the queue.")
            return
        self.queue.clear()
        self.queue_status.clear()
        self.manifest_job_id = ""
        self.profile_discovery_data = {}
        self.refresh_queue()
        self.status_var.set("Queue cleared.")

    def mark_output_custom(self, _event=None):
        self.output_dir_custom = True

    def browse_output(self):
        initial = self.output_var.get().strip() or str(self.default_output_path)
        if not Path(initial).expanduser().exists():
            initial = str(application_dir())
        selected = filedialog.askdirectory(title="Choose output folder", initialdir=initial)
        if selected:
            self.output_var.set(selected)
            self.output_dir_custom = True
            self.save_settings()

    def export_manifest(self):
        if not self.queue:
            messagebox.showinfo("Empty queue", "Add assets before exporting a manifest.")
            return
        path = filedialog.asksaveasfilename(
            title="Export asset_manifest.json",
            defaultextension=".json",
            initialfile="asset_manifest.json",
            filetypes=[("JSON files", "*.json")]
        )
        if not path:
            return
        data = {
            "schema_version": "1.0",
            "job_id": self.manifest_job_id or f"asset-job-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            "assets": [clean_manifest_asset(a) for a in self.queue],
        }
        if self.profile_discovery_data:
            data["profile_discovery"] = self.profile_discovery_data
        try:
            write_json(Path(path), data)
        except OSError as exc:
            messagebox.showerror("Could not export manifest", str(exc))
            return
        self.status_var.set(f"Manifest exported: {path}")

    def start_downloads(self, assets_to_download=None):
        if self.busy:
            return
        if not self.queue:
            messagebox.showwarning("Empty queue", "Add at least one asset before starting.")
            return
        output_dir = Path(self.output_var.get().strip()).expanduser()
        if not self.output_var.get().strip():
            messagebox.showwarning("Output folder required", "Choose an output folder.")
            return
        try:
            max_mb = max(1, min(2048, int(self.max_mb_var.get())))
            instagram_delay = max(5, min(60, int(self.instagram_delay_var.get() or "12")))
            manifest = {
                "schema_version": "1.0",
                "job_id": self.manifest_job_id or f"asset-job-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                "assets": [clean_manifest_asset(a) for a in (self.queue if assets_to_download is None else assets_to_download)],
            }
            if self.profile_discovery_data:
                manifest["profile_discovery"] = self.profile_discovery_data
            validated = engine.validate_manifest(manifest)
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Cannot start download", str(exc))
            return

        if not self.save_settings():
            return
        self.active_download_ids = {asset["asset_id"] for asset in validated}
        self.busy = True
        self.start_button.configure(state="disabled", text="Downloading…")
        self.update_retry_button()
        self.status_var.set(f"Starting {len(validated)} asset(s)…")
        self.progress_var.set(0)
        instagram_auth_settings = self.get_instagram_auth_settings()

        download_policy = {
            "instagram_delay_seconds": instagram_delay,
            "max_retries": 1,
            "backoff_base_seconds": 20,
            "max_backoff_seconds": 300,
            "stop_after_consecutive_failures": 2,
        }

        def progress_callback(done, total, result):
            self.dispatch(self.on_asset_progress, done, total, result)

        def status_callback(message):
            self.dispatch(self.on_download_status, message)

        def worker():
            try:
                package_dir, zip_path, results = engine.run_job(
                    manifest, validated, output_dir, max_mb * 1024 * 1024,
                    progress_callback=progress_callback,
                    instagram_auth=instagram_auth_settings,
                    download_policy=download_policy,
                    status_callback=status_callback,
                )
                self.dispatch(self.on_download_finished, package_dir, zip_path, results)
            except Exception as exc:
                self.dispatch(self.on_download_error, str(exc), traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()

    def on_download_status(self, message):
        self.status_var.set(message)

    def on_asset_progress(self, done, total, result):
        self.progress_var.set((done / max(total, 1)) * 100)
        asset_id = result.get("asset_id", "")
        status = str(result.get("status", "failed")).lower()

        if status in SUCCESSFUL_QUEUE_STATUSES:
            self.queue, self.queue_status, removed_ids = prune_successful_queue_items(
                self.queue, self.queue_status, [result]
            )
            if removed_ids:
                self.refresh_queue()
            self.status_var.set(
                f"Completed {done} of {total}: {asset_id} — "
                f"{'already present' if status == 'duplicate' else 'downloaded'}; removed from queue."
            )
        else:
            self.queue_status[asset_id] = status.capitalize()
            self.refresh_queue()
            self.status_var.set(f"Processed {done} of {total}: {asset_id} — {status}")
            self.update_retry_button()

    def on_download_finished(self, package_dir, zip_path, results):
        self.busy = False
        self.queue, self.queue_status, completed_ids = prune_successful_queue_items(
            self.queue, self.queue_status, results
        )
        self.active_download_ids.clear()
        self.refresh_queue()
        try:
            if hasattr(self, "start_button") and self.start_button.winfo_exists():
                self.start_button.configure(state="normal" if self.queue else "disabled", text="Download assets")
        except Exception:
            pass
        assets_downloaded = sum(1 for r in results if r["status"] == "downloaded")
        assets_duplicate = sum(1 for r in results if r["status"] == "duplicate")
        files_saved = sum(
            1 for r in results for f in r.get("files", [])
            if f.get("status") == "downloaded"
        )
        failed = sum(1 for r in results if r["status"] == "failed")
        deferred = sum(1 for r in results if r["status"] == "deferred")
        retained = len(self.queue)
        self.status_var.set(
            f"Finished — {assets_downloaded} downloaded; {assets_duplicate} already present; "
            f"{files_saved} files saved; {failed} failed; {deferred} deferred. "
            f"{len(completed_ids)} completed item(s) removed from queue."
        )
        history = self.load_history()
        history.insert(0, {
            "time": datetime.now().isoformat(timespec="seconds"),
            "zip_path": str(zip_path),
            "folder": str(package_dir),
            "assets": len(results),
            "assets_downloaded": assets_downloaded,
            "assets_duplicate": assets_duplicate,
            "files_saved": files_saved,
            "downloaded": files_saved,
            "failed": failed,
            "deferred": deferred,
        })
        try:
            write_json(self.history_path, history[:50])
        except OSError as exc:
            messagebox.showwarning("Package saved", f"The download package was saved, but history could not be updated:\n{exc}")
        self.update_retry_button()
        messagebox.showinfo(
            "Download complete",
            f"Package created:\n{zip_path}\n\n"
            f"Assets downloaded: {assets_downloaded}\n"
            f"Files saved: {files_saved}\n"
            f"Duplicate-only assets: {assets_duplicate}\n"
            f"Failed assets: {failed}\n"
            f"Deferred assets: {deferred}\n"
            f"Removed from queue: {len(completed_ids)}\n"
            f"Remaining in queue: {retained}\n\n"
            "Failed/deferred assets and any newly added items remain in the queue."
        )
        if messagebox.askyesno("Open output folder?", "Would you like to open the package folder now?"):
            self.open_path(str(package_dir))

    def on_download_error(self, message, details):
        self.busy = False
        self.active_download_ids.clear()
        try:
            if hasattr(self, "start_button") and self.start_button.winfo_exists():
                self.start_button.configure(state="normal" if self.queue else "disabled", text="Download assets")
        except Exception:
            pass
        self.status_var.set("Download job stopped with an error.")
        self.update_retry_button()
        messagebox.showerror("Download error", engine.redact_sensitive_text(message + "\n\n" + details[-1800:]))

    def load_history(self):
        return [item for item in read_json(self.history_path, list, []) if isinstance(item, dict)]

    def open_path(self, path):
        try:
            if os.name == "nt":
                os.startfile(path)
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", path])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            messagebox.showerror("Could not open folder", str(exc))

    def show_history(self):
        self.clear_content()
        self.scrollable_page()
        self.heading("Download History", "Review packages created by this desktop application.")
        entries = self.load_history()
        if not entries:
            inner = self.card(self.content)
            tk.Label(inner, text="No download history yet.", bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 11)).pack(anchor="w")
            return
        for item in entries[:30]:
            card = self.card(self.content, padx=16, pady=12)
            row = tk.Frame(card, bg=PANEL)
            row.pack(fill="x")
            info = tk.Frame(row, bg=PANEL)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=Path(item.get("zip_path", "package.zip")).name,
                     bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 10)).pack(anchor="w")
            files_saved = item.get("files_saved", item.get("downloaded", 0))
            assets_downloaded = item.get("assets_downloaded", "—")
            deferred = item.get("deferred", 0)
            detail = (
                f"{item.get('time','')}  •  {assets_downloaded} assets  •  "
                f"{files_saved} files saved  •  {item.get('failed',0)} failed  •  {deferred} deferred"
            )
            tk.Label(info, text=detail,
                     bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))
            self.styled_button(row, "Open package", lambda p=item.get("folder", ""): self.open_path(p), small=True).pack(side="right")

    def show_settings(self):
        self.clear_content()
        self.scrollable_page()
        self.heading("Settings", "Configure local Instagram authentication and download defaults.")

        auth_card = self.card(self.content)
        tk.Label(auth_card, text="INSTAGRAM SESSION", bg=PANEL, fg=TEXT,
                 font=("Segoe UI Semibold", 11)).pack(anchor="w")
        tk.Label(
            auth_card,
            text="Use your existing Firefox login locally. GTL AI does not receive, store, or upload your Instagram cookies.",
            bg=PANEL, fg=MUTED, wraplength=760, justify="left"
        ).pack(anchor="w", pady=(6, 12))

        radio_row = tk.Frame(auth_card, bg=PANEL)
        radio_row.pack(fill="x", pady=(0, 10))
        tk.Radiobutton(
            radio_row, text="Use Firefox browser session (recommended)",
            variable=self.instagram_auth_mode_var, value="firefox",
            bg=PANEL, fg=TEXT, selectcolor=PANEL_2, activebackground=PANEL,
            activeforeground=TEXT
        ).pack(anchor="w")
        tk.Radiobutton(
            radio_row, text="Anonymous public access",
            variable=self.instagram_auth_mode_var, value="anonymous",
            bg=PANEL, fg=TEXT, selectcolor=PANEL_2, activebackground=PANEL,
            activeforeground=TEXT
        ).pack(anchor="w", pady=(4, 0))

        tk.Label(auth_card, text="FIREFOX PROFILE", bg=PANEL, fg=MUTED,
                 font=("Segoe UI Semibold", 8)).pack(anchor="w", pady=(8, 5))
        profile_row = tk.Frame(auth_card, bg=PANEL)
        profile_row.pack(fill="x")
        choices = self.refresh_firefox_profile_choices()
        self.firefox_profile_combo = ttk.Combobox(
            profile_row, textvariable=self.firefox_profile_var,
            values=choices, state="readonly", width=52
        )
        self.firefox_profile_combo.pack(side="left", fill="x", expand=True)
        self.styled_button(
            profile_row, "Refresh profiles", self.refresh_firefox_profiles_ui, small=True
        ).pack(side="left", padx=(8, 0))
        self.styled_button(
            profile_row, "Open Instagram", self.open_instagram_in_firefox, small=True
        ).pack(side="left", padx=(8, 0))

        if self.firefox_profiles:
            profile_note = f"Detected {len(self.firefox_profiles)} Firefox profile(s) with a cookies database."
        else:
            profile_note = (
                "No Firefox profiles were detected automatically. Auto-detect will still be tried; "
                "if it fails, refresh profiles after signing into Instagram in Firefox."
            )
        tk.Label(auth_card, text=profile_note, bg=PANEL, fg=MUTED,
                 wraplength=760, justify="left").pack(anchor="w", pady=(9, 0))
        tk.Label(
            auth_card,
            text="Note: Firefox must have an active Instagram login in the selected profile. "
                 "If its cookie database is locked, close Firefox completely and retry.",
            bg=PANEL, fg=MUTED, wraplength=760, justify="left"
        ).pack(anchor="w", pady=(5, 0))

        inner = self.card(self.content)
        tk.Label(inner, text="DEFAULT OUTPUT FOLDER", bg=PANEL, fg=MUTED,
                 font=("Segoe UI Semibold", 8)).pack(anchor="w")
        row = tk.Frame(inner, bg=PANEL)
        row.pack(fill="x", pady=(7, 18))
        settings_output = tk.Entry(row, textvariable=self.output_var, bg="#0E121D", fg=TEXT,
                 insertbackground=TEXT, relief="flat", highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=PURPLE)
        settings_output.pack(
                     side="left", fill="x", expand=True, ipady=9
                 )
        settings_output.bind("<KeyRelease>", self.mark_output_custom)
        self.styled_button(row, "Browse", self.browse_output, small=True).pack(side="left", padx=(8, 0))
        tk.Label(inner, text="DELAY BETWEEN INSTAGRAM PUBLICATIONS (SECONDS)", bg=PANEL, fg=MUTED,
                 font=("Segoe UI Semibold", 8)).pack(anchor="w")
        tk.Entry(
            inner, textvariable=self.instagram_delay_var, bg="#0E121D", fg=TEXT,
            insertbackground=TEXT, relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=PURPLE, width=12
        ).pack(anchor="w", pady=(7, 6), ipady=8)
        tk.Label(
            inner,
            text="Default: 12 seconds (allowed: 5–60). Temporary JSON/API failures receive at most one retry with backoff.",
            bg=PANEL, fg=MUTED, wraplength=760, justify="left"
        ).pack(anchor="w", pady=(0, 18))
        tk.Label(inner, text="MAXIMUM FILE SIZE (MB)", bg=PANEL, fg=MUTED,
                 font=("Segoe UI Semibold", 8)).pack(anchor="w")
        tk.Entry(inner, textvariable=self.max_mb_var, bg="#0E121D", fg=TEXT,
                 insertbackground=TEXT, relief="flat", highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=PURPLE, width=12).pack(
                     anchor="w", pady=(7, 18), ipady=8
                 )
        self.styled_button(inner, "Save Settings", self.save_settings, primary=True).pack(anchor="w")

        note = self.card(self.content)
        tk.Label(note, text="Access and safety", bg=PANEL, fg=TEXT,
                 font=("Segoe UI Semibold", 11)).pack(anchor="w")
        tk.Label(
            note,
            text="The app does not bypass private-account restrictions, login walls, CAPTCHAs or rate limits. "
                 "A browser session can help with authentication, but Instagram may still reject its "
                 "posts/Reels GraphQL API independently of cookies.",
            bg=PANEL, fg=MUTED, wraplength=760, justify="left"
        ).pack(anchor="w", pady=(7, 0))

    def refresh_firefox_profiles_ui(self):
        choices = self.refresh_firefox_profile_choices()
        if hasattr(self, "firefox_profile_combo"):
            self.firefox_profile_combo.configure(values=choices)
        count = len(self.firefox_profiles)
        messagebox.showinfo(
            "Firefox profiles",
            f"Found {count} Firefox profile(s) with cookies.sqlite."
            if count else
            "No Firefox profiles with cookies.sqlite were found. Check that Firefox is installed and has been opened at least once."
        )

    def show_about(self):
        self.clear_content()
        self.scrollable_page()
        self.heading("About", "A focused media-acquisition utility for GTL AI workflows.")
        inner = self.card(self.content)
        try:
            with Image.open(resource_path("assets/GTL_AI_logo.png")) as logo:
                logo.thumbnail((520, 170), Image.Resampling.LANCZOS)
                self.about_logo = ImageTk.PhotoImage(logo.copy())
            tk.Label(inner, image=self.about_logo, bg=PANEL).pack(anchor="w", pady=(4, 20))
        except OSError:
            pass
        tk.Label(inner, text="GTL AI ASSET DOWNLOADER", bg=PANEL, fg=TEXT,
                 font=("Segoe UI Semibold", 16)).pack(anchor="w")
        tk.Label(inner, text=f"Version {APP_VERSION}", bg=PANEL, fg=PURPLE,
                 font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(5, 16))
        tk.Label(inner, text="Supported sources in this release", bg=PANEL, fg=TEXT,
                 font=("Segoe UI Semibold", 10)).pack(anchor="w")
        tk.Label(inner, text="• Instagram public profiles: latest posts + Reels\n• Instagram profile discovery using username URLs and local Firefox session\n• Instagram post, carousel and Reel URLs\n• Direct image and video URLs\n• Canonical asset_manifest.json import/export\n• ZIP packaging, SHA-256 deduplication and extraction reports",
                 bg=PANEL, fg=MUTED, justify="left", font=("Segoe UI", 10)).pack(anchor="w", pady=(7, 16))
        tk.Label(inner, text="Profile discovery uses the exact username URL and can read cookies locally from a selected Firefox profile via gallery-dl. Instagram downloads are sequential with a configurable cooldown, one conservative retry for transient errors, structured sanitized diagnostics and a circuit breaker for repeated access failures. Cookies are not copied into GTL AI storage. Login walls, CAPTCHAs and rate limits are not bypassed.",
                 bg=PANEL, fg=MUTED, wraplength=760, justify="left").pack(anchor="w")


def _attach_child_pipe(fd: int, win_std_handle: int):
    """Attach a frozen windowed helper's Python stream to its inherited pipe."""
    if os.name == "nt":
        try:
            import ctypes
            import msvcrt
            get_std_handle = ctypes.windll.kernel32.GetStdHandle
            get_std_handle.argtypes = [ctypes.c_ulong]
            get_std_handle.restype = ctypes.c_void_p
            handle = get_std_handle(ctypes.c_ulong(win_std_handle).value)
            if handle not in (None, 0, -1):
                os_fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
                return os.fdopen(
                    os_fd, "w", encoding="utf-8", errors="replace",
                    buffering=1
                )
        except Exception:
            pass

    try:
        return os.fdopen(
            fd, "w", encoding="utf-8", errors="replace",
            buffering=1, closefd=False
        )
    except Exception:
        return open(os.devnull, "w", encoding="utf-8", errors="replace")


def run_gallery_dl_helper():
    # The frozen --windowed executable normally has sys.stdout/stderr=None.
    # When the parent launches this helper with capture_output=True, Windows
    # supplies inherited pipe handles. Reattach those handles instead of
    # discarding gallery-dl's JSON output into os.devnull.
    if sys.stdout is None:
        sys.stdout = _attach_child_pipe(1, -11)  # STD_OUTPUT_HANDLE
    if sys.stderr is None:
        sys.stderr = _attach_child_pipe(2, -12)  # STD_ERROR_HANDLE
    sys.argv = ["gallery-dl"] + sys.argv[2:]
    runpy.run_module("gallery_dl", run_name="__main__", alter_sys=True)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--gallery-dl-helper":
        run_gallery_dl_helper()
        return
    root = tk.Tk()
    AssetStudio(root)
    root.mainloop()


if __name__ == "__main__":
    main()
