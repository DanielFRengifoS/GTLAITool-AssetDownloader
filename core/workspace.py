"""The downloader workspace, kept alive while other pages are open."""
import tkinter as tk
from tkinter import ttk

from . import ui


class WorkspaceUI:
    def show_downloader(self):
        if self.downloader_content is not None:
            if self.content is not self.downloader_content:
                for child in self.page_host.winfo_children():
                    if child is not self.downloader_content:
                        child.destroy()
                self.content = self.downloader_content
                self.content.pack(fill="both", expand=True)
            return
        self.clear_content()
        self.downloader_content = self.content
        self.nav_buttons["Downloader"].configure(bg=ui.ACCENT_DARK, fg=ui.ACCENT,
                                                image=self.get_icon("Downloader", ui.ACCENT))
        self.heading("Asset workspace", "Find it. Queue it. Keep it organized.")
        self.stats = {}
        stats_row = tk.Frame(self.content, bg=ui.BG)
        self.stats_row = stats_row
        stats_row.pack(fill="x", pady=(0, 18))
        for index, (key, title, color) in enumerate([
            ("total", "ASSETS IN QUEUE", ui.TEXT), ("instagram", "INSTAGRAM", ui.ACCENT),
            ("direct", "DIRECT MEDIA", ui.BLUE), ("attention", "NEEDS ATTENTION", ui.AMBER),
        ]):
            stats_row.columnconfigure(index, weight=1, uniform="stat")
            stat = tk.Frame(stats_row, bg=ui.PANEL, highlightbackground=ui.BORDER, highlightthickness=1)
            stat.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 6, 0 if index == 3 else 6))
            tk.Label(stat, text=title, bg=ui.PANEL, fg=ui.MUTED,
                     font=("Segoe UI Semibold", 8)).pack(anchor="w", padx=16, pady=(11, 2))
            variable = tk.StringVar(value="0")
            self.stats[key] = variable
            tk.Label(stat, textvariable=variable, bg=ui.PANEL, fg=color,
                     font=("Segoe UI Semibold", 21)).pack(anchor="w", padx=16, pady=(0, 9))

        tabs = tk.Frame(self.content, bg=ui.BG)
        self.source_tabs = tabs
        tabs.pack(fill="x", pady=(0, 12))
        self.mode_buttons = {}
        for mode, text in [("Instagram", "Instagram"), ("Instagram Profile", "Profile extractor"),
                           ("Direct URLs", "Direct URLs"), ("Manifest", "JSON manifest")]:
            button = self.styled_button(tabs, text, lambda m=mode: self.switch_mode(m), small=True)
            button.configure(compound="left", padx=12, pady=9)
            button.pack(side="left", padx=(0, 6))
            button.unbind("<Enter>")
            button.unbind("<Leave>")
            self.mode_buttons[mode] = button

        self.input_card = tk.Frame(self.content, bg=ui.PANEL, highlightbackground=ui.BORDER, highlightthickness=1)
        self.input_card.pack(fill="x", pady=(0, 14))
        self.source_canvas = tk.Canvas(self.input_card, bg=ui.PANEL, bd=0, highlightthickness=0, height=210)
        self.source_scroll = ttk.Scrollbar(self.input_card, orient="vertical", command=self.source_canvas.yview)
        self.source_canvas.configure(yscrollcommand=self.source_scroll.set)
        self.source_canvas.pack(side="left", fill="x", expand=True)
        self.source_scroll.pack(side="right", fill="y")
        self.input_body = tk.Frame(self.source_canvas, bg=ui.PANEL)
        self.source_window = self.source_canvas.create_window((0, 0), window=self.input_body, anchor="nw")
        self.source_canvas.bind("<Configure>", lambda e: self.source_canvas.itemconfigure(self.source_window, width=e.width))
        self.input_body.bind("<Configure>", self.resize_source_panel)
        self.content.bind("<Configure>", self.resize_source_panel)
        self.switch_mode(self.input_mode.get())

        # Reserve the footer before allocating the expanding queue.
        footer = tk.Frame(self.content, bg=ui.BG)
        footer.pack(side="bottom", fill="x")
        output_row = tk.Frame(footer, bg=ui.PANEL, highlightbackground=ui.BORDER, highlightthickness=1)
        output_row.pack(fill="x", pady=(0, 10))
        tk.Label(output_row, image=self.get_icon("folder", ui.ACCENT, 19), bg=ui.PANEL).pack(side="left", padx=(14, 8))
        tk.Label(output_row, text="Save to", bg=ui.PANEL, fg=ui.MUTED,
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 10))
        self.output_entry = tk.Entry(output_row, textvariable=self.output_var, bg=ui.PANEL, fg=ui.TEXT,
                                     insertbackground=ui.TEXT, relief="flat")
        self.output_entry.pack(side="left", fill="x", expand=True, ipady=12)
        self.output_entry.bind("<KeyRelease>", self.mark_output_custom)
        self.output_entry.bind("<FocusOut>", lambda _event: self.save_settings())
        self.styled_button(output_row, "Browse…", self.browse_output, small=True).pack(side="right", padx=6, pady=5)
        self.progress = ttk.Progressbar(footer, variable=self.progress_var, maximum=100,
                                        style="Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(0, 7))
        progress_row = tk.Frame(footer, bg=ui.BG)
        progress_row.pack(fill="x")
        self.export_button = self.styled_button(progress_row, "Export manifest", self.export_manifest, small=True)
        self.export_button.pack(side="right", padx=(12, 0))
        self.status_label = tk.Label(progress_row, textvariable=self.status_var, bg=ui.BG, fg=ui.MUTED,
                                     font=("Segoe UI", 9), anchor="w", wraplength=650, justify="left")
        self.status_label.pack(side="left", fill="x", expand=True)
        self.status_label.bind("<Configure>", lambda e: self.status_label.configure(wraplength=max(150, e.width)))

        queue_header = tk.Frame(self.content, bg=ui.BG)
        queue_header.pack(fill="x", pady=(2, 9))
        self.queue_title = tk.StringVar(value="Download queue")
        tk.Label(queue_header, textvariable=self.queue_title,
                 bg=ui.BG, fg=ui.TEXT, font=("Segoe UI Semibold", 13)).pack(side="left")
        self.styled_button(queue_header, "Remove selected", self.remove_selected, small=True).pack(side="right", padx=(8, 0))
        self.styled_button(queue_header, "Clear queue", self.clear_queue, small=True).pack(side="right")
        self.retry_failed_button = self.styled_button(queue_header, "Retry failed / deferred", self.retry_failed_only, small=True)
        self.retry_failed_button.pack(side="right", padx=(0, 8))

        table_frame = tk.Frame(self.content, bg=ui.PANEL, highlightbackground=ui.BORDER, highlightthickness=1)
        table_frame.pack(fill="both", expand=True, pady=(0, 12))
        columns = ("source", "asset", "selection", "status")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended", height=4)
        for column, title, width in [("source", "SOURCE", 110), ("asset", "ASSET / URL", 350),
                                     ("selection", "SELECTION", 110), ("status", "STATUS", 105)]:
            self.tree.heading(column, text=title, anchor="w")
            self.tree.column(column, width=width, minwidth=100, stretch=column == "asset")
        self.tree.tag_configure("even", background=ui.PANEL)
        self.tree.tag_configure("odd", background="#152330")
        self.tree.tag_configure("failed", foreground=ui.RED)
        self.tree.tag_configure("deferred", foreground=ui.AMBER)
        self.tree.bind("<Delete>", lambda _e: self.remove_selected())
        self.tree.bind("<Control-a>", self.select_all_queue)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        scroll.pack(side="right", fill="y", padx=(0, 8), pady=10)
        self.queue_empty = tk.Frame(table_frame, bg=ui.PANEL)
        tk.Label(self.queue_empty, image=self.get_icon("download", ui.ACCENT, 32), bg=ui.PANEL).pack(pady=(0, 7))
        tk.Label(self.queue_empty, text="Your next collection starts here", bg=ui.PANEL, fg=ui.TEXT,
                 font=("Segoe UI Semibold", 12)).pack()
        tk.Label(self.queue_empty, text="Add a publication, paste media URLs, or import a manifest.",
                 bg=ui.PANEL, fg=ui.MUTED, font=("Segoe UI", 9)).pack(pady=(5, 0))
        self.refresh_queue()

    def select_all_queue(self, _event=None):
        self.tree.selection_set(self.tree.get_children())
        return "break"

    def resize_source_panel(self, _event=None):
        compact = self.downloader_content.winfo_height() < 800
        if compact and self.stats_row.winfo_manager():
            self.stats_row.pack_forget()
        elif not compact and not self.stats_row.winfo_manager():
            self.stats_row.pack(fill="x", pady=(0, 18), before=self.source_tabs)
        self.source_canvas.configure(scrollregion=self.source_canvas.bbox("all"))
        maximum = max(120, min(380, self.downloader_content.winfo_height() - (495 if compact else 600)))
        self.source_canvas.configure(height=max(1, min(self.input_body.winfo_reqheight(), maximum)))
        overflowing = self.input_body.winfo_reqheight() > maximum
        if overflowing and not self.source_scroll.winfo_manager():
            self.source_scroll.pack(side="right", fill="y")
        elif not overflowing and self.source_scroll.winfo_manager():
            self.source_scroll.pack_forget()

    def switch_mode(self, mode):
        self.input_mode.set(mode)
        for name, button in self.mode_buttons.items():
            active = name == mode
            button.configure(fg=ui.ACCENT if active else ui.MUTED,
                             bg=ui.ACCENT_DARK if active else ui.BG,
                             image=self.get_icon(name, ui.ACCENT if active else ui.MUTED, 17))
        self.render_input_mode()
