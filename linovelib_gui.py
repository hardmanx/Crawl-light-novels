# -*- coding: utf-8 -*-
"""
可视化界面：加载小说目录、勾选卷章、爬取并生成 EPUB。

界面直接复用 linovelib_crawler.py 的解析和 EPUB 生成能力，不绕过登录、
验证码、付费、反爬或访问限制。
"""

import contextlib
import os
import queue
import shutil
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import linovelib_crawler as crawler_module
from linovelib_crawler import (
    LinovelibVolumeEpubCrawler,
    SAVE_DIR,
    safe_name,
)


APP_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = SAVE_DIR
CHECKED = "☑"
UNCHECKED = "☐"


class QueueWriter:
    def __init__(self, log_queue):
        self.log_queue = log_queue
        self._buffer = ""

    def write(self, text):
        if not text:
            return

        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self.log_queue.put(("log", line + "\n"))

    def flush(self):
        if self._buffer:
            self.log_queue.put(("log", self._buffer))
            self._buffer = ""


class CrawlerGui(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("轻小说 EPUB 工作台")
        self.geometry("1260x780")
        self.minsize(900, 560)

        self.log_queue = queue.Queue()
        self.worker = None
        self.stop_requested = False
        self.is_busy = False

        self.book_info = None
        self.volumes = []
        self.checked_chapters = set()
        self.volume_items = {}
        self.chapter_items = {}

        self._setup_style()
        self._build_layout()
        self._set_running(False)
        self._poll_queue()

    def _setup_style(self):
        self.configure(bg="#0f1416")

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background="#0f1416", foreground="#edf2f4", bordercolor="#2a3438")
        style.configure("Root.TFrame", background="#0f1416")
        style.configure("Panel.TFrame", background="#172024")
        style.configure("Inset.TFrame", background="#10171a")
        style.configure("TLabel", background="#172024", foreground="#d7e0e4")
        style.configure("Title.TLabel", background="#0f1416", foreground="#fbf7ef")
        style.configure("Subtle.TLabel", background="#0f1416", foreground="#8ea1a9")
        style.configure("PanelTitle.TLabel", background="#172024", foreground="#fbf7ef")
        style.configure("Hint.TLabel", background="#172024", foreground="#94a3ad")
        style.configure(
            "TEntry",
            fieldbackground="#0c1214",
            foreground="#f8fafc",
            insertcolor="#f8fafc",
            padding=7,
        )
        style.configure("TCheckbutton", background="#172024", foreground="#d7e0e4")
        style.configure("Accent.TButton", background="#f0c35a", foreground="#121212", padding=(16, 10))
        style.map("Accent.TButton", background=[("active", "#ffd56a"), ("disabled", "#6b5b32")])
        style.configure("Secondary.TButton", background="#263339", foreground="#edf2f4", padding=(12, 9))
        style.map("Secondary.TButton", background=[("active", "#33444c"), ("disabled", "#1c2529")])
        style.configure("Danger.TButton", background="#7f1d1d", foreground="#fee2e2", padding=(12, 9))
        style.map("Danger.TButton", background=[("active", "#991b1b"), ("disabled", "#3d1d1d")])
        style.configure("Treeview", background="#10171a", fieldbackground="#10171a", foreground="#dbe7ec", rowheight=30)
        style.configure("Treeview.Heading", background="#223037", foreground="#f8fafc", padding=8)
        style.map("Treeview", background=[("selected", "#33444c")])
        style.configure("Horizontal.TProgressbar", background="#f0c35a", troughcolor="#263339")

    def _build_layout(self):
        root = ttk.Frame(self, style="Root.TFrame", padding=20)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        header = ttk.Frame(root, style="Root.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(
            header,
            text="轻小说 EPUB 工作台",
            font=("Microsoft YaHei UI", 26, "bold"),
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w")

        self.status_var = tk.StringVar(value="待命")
        ttk.Label(
            header,
            textvariable=self.status_var,
            font=("Microsoft YaHei UI", 12, "bold"),
            style="Subtle.TLabel",
        ).grid(row=0, column=1, sticky="e", padx=(16, 0))

        ttk.Label(
            root,
            text="从小说 ID 或名称开始，加载目录，勾选卷章，然后在同一个窗口里完成爬取与生成。",
            font=("Microsoft YaHei UI", 10),
            style="Subtle.TLabel",
        ).grid(row=1, column=0, sticky="ew", pady=(6, 18))

        main = ttk.Frame(root, style="Root.TFrame")
        main.grid(row=2, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1, minsize=280)
        main.columnconfigure(1, weight=4, minsize=360)
        main.columnconfigure(2, weight=2, minsize=280)
        main.rowconfigure(0, weight=1)

        left_shell = ttk.Frame(main, style="Panel.TFrame")
        left_shell.grid(row=0, column=0, sticky="nsew")
        left = self._make_scrollable_panel(left_shell)

        center = ttk.Frame(main, style="Panel.TFrame", padding=18)
        center.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        center.columnconfigure(0, weight=1)
        center.rowconfigure(1, weight=1)

        right = ttk.Frame(main, style="Panel.TFrame", padding=18)
        right.grid(row=0, column=2, sticky="nsew", padx=(16, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        self._build_left_panel(left)
        self._build_center_panel(center)
        self._build_right_panel(right)

    def _make_scrollable_panel(self, parent):
        canvas = tk.Canvas(parent, bg="#172024", highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas, style="Panel.TFrame", padding=18)
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def sync_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def sync_content_width(event):
            canvas.itemconfigure(window_id, width=event.width)

        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        content.bind("<Configure>", sync_scroll_region)
        canvas.bind("<Configure>", sync_content_width)
        canvas.bind("<Enter>", lambda _event: canvas.bind_all("<MouseWheel>", on_mousewheel))
        canvas.bind("<Leave>", lambda _event: canvas.unbind_all("<MouseWheel>"))

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return content

    def _build_left_panel(self, parent):
        ttk.Label(parent, text="1. 小说数据", font=("Microsoft YaHei UI", 15, "bold"), style="PanelTitle.TLabel").pack(anchor="w")
        ttk.Label(parent, text="推荐输入小说 ID；也可以输入小说名称，程序会先查首页，再扫描文库页。", wraplength=300, style="Hint.TLabel").pack(anchor="w", pady=(6, 16))

        self.keyword_var = tk.StringVar(value="2906")
        self.delay_var = tk.StringVar(value="1.5")
        self.scan_pages_var = tk.StringVar(value="5")
        self.output_dir_var = tk.StringVar(value=str(DOWNLOADS_DIR))
        self.download_images_var = tk.BooleanVar(value=False)
        self.include_spoiler_images_var = tk.BooleanVar(value=False)
        self._entry(parent, "小说ID / 名称", self.keyword_var)

        grid = ttk.Frame(parent, style="Panel.TFrame")
        grid.pack(fill="x")
        self._grid_entry(grid, "请求间隔秒", self.delay_var, 0, 0)
        self._grid_entry(grid, "搜索页数", self.scan_pages_var, 0, 1)

        ttk.Label(parent, text="下载目录").pack(anchor="w", pady=(4, 0))
        output_row = ttk.Frame(parent, style="Panel.TFrame")
        output_row.pack(fill="x", pady=(6, 10))
        ttk.Entry(output_row, textvariable=self.output_dir_var).pack(side="left", fill="x", expand=True, ipady=4)
        ttk.Button(output_row, text="选择", style="Secondary.TButton", command=self.choose_output_dir).pack(side="left", padx=(8, 0))

        self.download_images_button = ttk.Button(
            parent,
            text="☐ 下载并嵌入图片（更慢）",
            style="Secondary.TButton",
            command=self.toggle_download_images,
        )
        self.download_images_button.pack(fill="x", pady=(0, 8))
        self.include_spoiler_images_button = ttk.Button(
            parent,
            text="☐ 包含剧透完整插图",
            style="Secondary.TButton",
            command=self.toggle_include_spoiler_images,
        )
        self.include_spoiler_images_button.pack(fill="x", pady=(0, 8))
        ttk.Label(
            parent,
            text="速度建议：普通文本章节用 1-2 秒；遇到 403/429 时调高间隔。剧透完整插图只在勾选图片下载时嵌入 EPUB。",
            wraplength=300,
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(0, 6))

        self.load_button = ttk.Button(parent, text="加载小说目录", style="Accent.TButton", command=self.load_catalog)
        self.load_button.pack(fill="x", pady=(12, 10))

        ttk.Label(parent, text="2. 执行", font=("Microsoft YaHei UI", 15, "bold"), style="PanelTitle.TLabel").pack(anchor="w", pady=(18, 0))
        ttk.Label(parent, text="在右侧卷章列表里单击卷或章节进行选择，然后点击开始。", wraplength=300, style="Hint.TLabel").pack(anchor="w", pady=(6, 10))
        self.selected_count_var = tk.StringVar(value="已选择 0 章")
        ttk.Label(parent, textvariable=self.selected_count_var, style="Hint.TLabel").pack(anchor="w", pady=(6, 12))

        self.start_button = ttk.Button(parent, text="请先加载目录", style="Accent.TButton", command=self.start_selected)
        self.start_button.pack(fill="x")
        self.stop_button = ttk.Button(parent, text="停止任务", style="Danger.TButton", command=self.request_stop)
        self.stop_button.pack(fill="x", pady=(10, 0))

        ttk.Button(parent, text="打开下载目录", style="Secondary.TButton", command=self.open_downloads).pack(fill="x", pady=(18, 0))

        self.progress = ttk.Progressbar(parent, mode="indeterminate")
        self.progress.pack(fill="x", pady=(18, 0))

    def _build_center_panel(self, parent):
        top = ttk.Frame(parent, style="Panel.TFrame")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        top.columnconfigure(0, weight=1)

        ttk.Label(top, text="卷章选择", font=("Microsoft YaHei UI", 15, "bold"), style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.start_button_top = ttk.Button(top, text="请先加载目录", style="Accent.TButton", command=self.start_selected)
        self.start_button_top.grid(row=0, column=3, sticky="e", padx=(8, 0))
        ttk.Button(top, text="全选", style="Secondary.TButton", command=self.select_all).grid(row=0, column=2, sticky="e", padx=(8, 0))
        ttk.Button(top, text="清空选择", style="Secondary.TButton", command=self.clear_selection).grid(row=0, column=1, sticky="e")

        columns = ("mark", "title", "count", "url")
        self.chapter_tree = ttk.Treeview(parent, columns=columns, show="tree headings", selectmode="browse")
        self.chapter_tree.heading("#0", text="网站卷章")
        self.chapter_tree.heading("mark", text="选择")
        self.chapter_tree.heading("title", text="标题")
        self.chapter_tree.heading("count", text="数量")
        self.chapter_tree.heading("url", text="URL")
        self.chapter_tree.column("#0", width=360, stretch=True)
        self.chapter_tree.column("mark", width=56, anchor="center", stretch=False)
        self.chapter_tree.column("title", width=240)
        self.chapter_tree.column("count", width=70, anchor="center", stretch=False)
        self.chapter_tree.column("url", width=220)
        self.chapter_tree.grid(row=1, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(parent, command=self.chapter_tree.yview)
        self.chapter_tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=1, column=1, sticky="ns")

        self.chapter_tree.bind("<ButtonRelease-1>", self.on_tree_click)
        self.chapter_tree.bind("<Double-1>", self.on_tree_double_click)
        self.chapter_tree.bind("<space>", self.on_tree_space)

    def _build_right_panel(self, parent):
        ttk.Label(parent, text="实时日志", font=("Microsoft YaHei UI", 15, "bold"), style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(parent, text="加载目录、章节解析、图片下载、EPUB 生成都会显示在这里。", wraplength=330, style="Hint.TLabel").grid(row=1, column=0, sticky="ew", pady=(6, 12))

        log_frame = tk.Frame(parent, bg="#0b1012", highlightbackground="#2a3438", highlightthickness=1)
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame,
            bg="#0b1012",
            fg="#dbe7ec",
            insertbackground="#dbe7ec",
            relief="flat",
            wrap="word",
            padx=12,
            pady=12,
            font=("Consolas", 10),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")

        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.grid(row=0, column=1, sticky="ns")

        self.log_text.tag_configure("info", foreground="#f0c35a")
        self.log_text.tag_configure("success", foreground="#86efac")
        self.log_text.tag_configure("error", foreground="#fca5a5")

        actions = ttk.Frame(parent, style="Panel.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        ttk.Button(actions, text="清空日志", style="Secondary.TButton", command=self.clear_log).grid(row=0, column=0, sticky="ew")
        ttk.Button(actions, text="打开项目目录", style="Secondary.TButton", command=lambda: os.startfile(APP_DIR)).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        self._append_log("工作台已就绪。先加载小说目录，再勾选章节。\n", "info")

    def toggle_download_images(self):
        self.download_images_var.set(not self.download_images_var.get())
        self._refresh_download_images_button()

    def _refresh_download_images_button(self):
        mark = "☑" if self.download_images_var.get() else "☐"
        self.download_images_button.configure(text=f"{mark} 下载并嵌入图片（更慢）")

    def toggle_include_spoiler_images(self):
        self.include_spoiler_images_var.set(not self.include_spoiler_images_var.get())
        self._refresh_include_spoiler_images_button()

    def _refresh_include_spoiler_images_button(self):
        mark = "☑" if self.include_spoiler_images_var.get() else "☐"
        self.include_spoiler_images_button.configure(text=f"{mark} 包含剧透完整插图")

    def _entry(self, parent, label, variable):
        ttk.Label(parent, text=label).pack(anchor="w")
        entry = ttk.Entry(parent, textvariable=variable)
        entry.pack(fill="x", pady=(6, 12), ipady=4)
        return entry

    def _grid_entry(self, parent, label, variable, row, column):
        cell = ttk.Frame(parent, style="Panel.TFrame")
        cell.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0), pady=(0, 10))
        parent.columnconfigure(column, weight=1)
        ttk.Label(cell, text=label).pack(anchor="w")
        ttk.Entry(cell, textvariable=variable, width=12).pack(fill="x", pady=(5, 0), ipady=4)

    def _positive_int(self, raw, label, allow_empty=False):
        raw = raw.strip()
        if allow_empty and not raw:
            return None
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{label} 必须是整数。") from exc
        if value < 1:
            raise ValueError(f"{label} 必须大于等于 1。")
        return value

    def _number(self, raw, label, caster, minimum):
        raw = raw.strip()
        try:
            value = caster(raw)
        except ValueError as exc:
            raise ValueError(f"{label} 必须是数字。") from exc
        if value < minimum:
            raise ValueError(f"{label} 不能小于 {minimum}。")
        return value

    def _new_crawler(self):
        delay = self._number(self.delay_var.get(), "请求间隔秒", float, 0)
        scan_pages = self._number(self.scan_pages_var.get(), "搜索页数", int, 1)
        return LinovelibVolumeEpubCrawler(delay=delay, scan_pages=scan_pages)

    def _output_dir(self):
        raw_path = self.output_dir_var.get().strip()
        if not raw_path:
            raise ValueError("请先选择下载目录。")

        return Path(raw_path).expanduser()

    def choose_output_dir(self):
        selected = filedialog.askdirectory(
            title="选择下载目录",
            initialdir=self.output_dir_var.get().strip() or str(APP_DIR),
        )
        if selected:
            self.output_dir_var.set(selected)

    def load_catalog(self):
        if self._is_running():
            return

        keyword = self.keyword_var.get().strip()
        if not keyword:
            messagebox.showerror("缺少信息", "请填写小说 ID 或名称。")
            return

        try:
            crawler = self._new_crawler()
            self._output_dir()
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        self.clear_log()
        self._append_log("开始加载小说目录...\n", "info")
        self._set_running(True, "加载目录中")
        self.worker = threading.Thread(target=self._load_catalog_worker, args=(crawler, keyword), daemon=True)
        self.worker.start()

    def _load_catalog_worker(self, crawler, keyword):
        writer = QueueWriter(self.log_queue)
        try:
            with contextlib.redirect_stdout(writer):
                book_url = crawler.resolve_book_url(keyword)
                book_info = crawler.parse_book_info(book_url)
                volumes = crawler.build_volume_list(book_info)
            writer.flush()
            self.log_queue.put(("catalog_loaded", (book_info, volumes)))
        except Exception as exc:
            writer.flush()
            self.log_queue.put(("error", f"加载目录失败：{exc}"))

    def _render_catalog(self):
        self.chapter_tree.delete(*self.chapter_tree.get_children())
        self.volume_items.clear()
        self.chapter_items.clear()
        self.checked_chapters.clear()

        for volume_index, volume in enumerate(self.volumes, 1):
            volume_id = f"v:{volume_index}"
            volume_title = volume.get("title", "未命名卷")
            chapter_count = len(volume.get("chapters", []))
            item_id = self.chapter_tree.insert(
                "",
                "end",
                iid=volume_id,
                text=f"网站第 {volume_index} 项 | {volume_title}",
                values=(UNCHECKED, volume_title, f"{chapter_count} 章", volume.get("url", "")),
                open=True,
            )
            self.volume_items[volume_index] = item_id

            for chapter_index, chapter in enumerate(volume.get("chapters", []), 1):
                chapter_id = f"c:{volume_index}:{chapter_index}"
                chapter_title = chapter.get("title", f"章节 {chapter_index}")
                self.chapter_tree.insert(
                    item_id,
                    "end",
                    iid=chapter_id,
                    text=f"{chapter_index:03d} | {chapter_title}",
                    values=(UNCHECKED, chapter_title, "", chapter.get("url", "")),
                )
                self.chapter_items[(volume_index, chapter_index)] = chapter_id

        self._update_selected_count()

    def on_tree_click(self, event):
        item_id = self.chapter_tree.identify_row(event.y)
        if not item_id:
            return

        self._toggle_tree_item(item_id)

    def on_tree_double_click(self, _event):
        return "break"

    def on_tree_space(self, _event):
        item_id = self.chapter_tree.focus()
        self._toggle_tree_item(item_id)
        return "break"

    def _toggle_tree_item(self, item_id):
        if not item_id:
            return

        parts = item_id.split(":")
        if parts[0] == "v":
            volume_index = int(parts[1])
            chapters = self.volumes[volume_index - 1].get("chapters", [])
            keys = [(volume_index, chapter_index) for chapter_index in range(1, len(chapters) + 1)]
            should_check = any(key not in self.checked_chapters for key in keys)
            for key in keys:
                if should_check:
                    self.checked_chapters.add(key)
                else:
                    self.checked_chapters.discard(key)
        elif parts[0] == "c":
            key = (int(parts[1]), int(parts[2]))
            if key in self.checked_chapters:
                self.checked_chapters.remove(key)
            else:
                self.checked_chapters.add(key)

        self._refresh_marks()

    def _refresh_marks(self):
        for volume_index, volume in enumerate(self.volumes, 1):
            chapters = volume.get("chapters", [])
            keys = [(volume_index, chapter_index) for chapter_index in range(1, len(chapters) + 1)]
            checked_count = sum(1 for key in keys if key in self.checked_chapters)
            mark = CHECKED if checked_count == len(keys) and keys else UNCHECKED
            if 0 < checked_count < len(keys):
                mark = "◩"
            self._set_tree_mark(self.volume_items[volume_index], mark)

            for chapter_index in range(1, len(chapters) + 1):
                key = (volume_index, chapter_index)
                self._set_tree_mark(self.chapter_items[key], CHECKED if key in self.checked_chapters else UNCHECKED)

        self._update_selected_count()
        self._refresh_action_buttons()

    def _set_tree_mark(self, item_id, mark):
        values = list(self.chapter_tree.item(item_id, "values"))
        values[0] = mark
        self.chapter_tree.item(item_id, values=values)

    def select_all(self):
        self.checked_chapters.clear()
        for volume_index, volume in enumerate(self.volumes, 1):
            for chapter_index, _chapter in enumerate(volume.get("chapters", []), 1):
                self.checked_chapters.add((volume_index, chapter_index))
        self._refresh_marks()

    def clear_selection(self):
        self.checked_chapters.clear()
        self._refresh_marks()

    def start_selected(self):
        if self.is_busy:
            return

        if not self.book_info or not self.volumes:
            messagebox.showinfo("还没有目录", "请先加载小说目录。")
            return

        if not self.checked_chapters:
            messagebox.showinfo("没有选择章节", "请先在中间列表勾选要爬取的章节。")
            return

        try:
            crawler = self._new_crawler()
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        self.stop_requested = False
        self._set_running(True, "爬取中")
        self._append_log(f"\n开始爬取所选 {len(self.checked_chapters)} 章...\n", "info")
        selected = sorted(self.checked_chapters)
        self.worker = threading.Thread(target=self._crawl_selected_worker, args=(crawler, selected), daemon=True)
        self.worker.start()

    def _crawl_selected_worker(self, crawler, selected):
        writer = QueueWriter(self.log_queue)
        old_download_images = crawler_module.DOWNLOAD_IMAGES
        old_include_spoiler_images = crawler_module.INCLUDE_SPOILER_IMAGES
        try:
            crawler_module.DOWNLOAD_IMAGES = self.download_images_var.get()
            crawler_module.INCLUDE_SPOILER_IMAGES = self.include_spoiler_images_var.get()
            with contextlib.redirect_stdout(writer):
                generated_epubs = self._crawl_selected(crawler, selected)
            writer.flush()
            self.log_queue.put(("crawl_done", generated_epubs))
        except Exception as exc:
            writer.flush()
            self.log_queue.put(("error", f"爬取失败：{exc}"))
        finally:
            crawler_module.DOWNLOAD_IMAGES = old_download_images
            crawler_module.INCLUDE_SPOILER_IMAGES = old_include_spoiler_images

    def _crawl_selected(self, crawler, selected):
        output_dir = self._output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)

        book_title = safe_name(self.book_info["title"])
        book_dir = output_dir / book_title
        epubs_dir = book_dir / "epubs"
        image_dir = book_dir / "_epub_images_tmp"

        book_dir.mkdir(parents=True, exist_ok=True)
        epubs_dir.mkdir(parents=True, exist_ok=True)
        image_dir.mkdir(parents=True, exist_ok=True)

        selected_by_volume = {}
        for volume_index, chapter_index in selected:
            selected_by_volume.setdefault(volume_index, []).append(chapter_index)

        generated_epubs = []

        for volume_index in sorted(selected_by_volume):
            if self.stop_requested:
                print("已请求停止，后续章节不再处理。")
                break

            volume_info = self.volumes[volume_index - 1]

            print("\n" + "=" * 80)
            print(f"开始处理网站第 {volume_index} 项：{volume_info['title']}")
            print(f"分卷地址：{volume_info.get('url', '')}")
            print(f"本卷已选择章节数：{len(selected_by_volume[volume_index])}")
            print("=" * 80)

            chapter_results = []

            for chapter_index in selected_by_volume[volume_index]:
                if self.stop_requested:
                    print("已请求停止，当前卷剩余章节不再处理。")
                    break

                chapter = volume_info["chapters"][chapter_index - 1]

                try:
                    chapter_data = crawler.parse_chapter(
                        chapter=chapter,
                        chapter_dir=book_dir,
                        image_dir=image_dir,
                        volume_order=volume_index,
                        chapter_order=chapter_index,
                    )
                except Exception as exc:
                    print(f"\n章节解析失败：{chapter['title']}")
                    print(f"URL：{chapter['url']}")
                    print(f"原因：{exc}")
                    continue

                chapter_results.append(chapter_data)

            if not chapter_results:
                print(f"网站第 {volume_index} 项没有成功解析任何所选章节，跳过 EPUB。")
                continue

            volume_result = {
                "title": volume_info["title"],
                "url": volume_info["url"],
                "chapters": chapter_results,
            }

            print(f"\n开始生成网站第 {volume_index} 项 EPUB：{volume_info['title']}")
            epub_path = crawler.create_epub_for_volume(
                book_info=self.book_info,
                volume_result=volume_result,
                volume_index=volume_index,
                epubs_dir=epubs_dir,
            )
            generated_epubs.append(str(epub_path))
            print(f"网站第 {volume_index} 项 EPUB 已生成：{epub_path}")

        if image_dir.exists():
            shutil.rmtree(image_dir, ignore_errors=True)

        print("\n========== 任务完成 ==========")
        print(f"小说保存目录：{book_dir}")
        print(f"EPUB 输出目录：{epubs_dir}")
        print(f"是否下载图片：{crawler_module.DOWNLOAD_IMAGES}")
        print(f"是否包含剧透完整插图：{crawler_module.INCLUDE_SPOILER_IMAGES}")
        return generated_epubs

    def request_stop(self):
        if self.is_busy:
            self.stop_requested = True
            self._append_log("\n已请求停止。当前网络请求或章节处理结束后会停止。\n", "error")
            self.status_var.set("停止中")

    def _is_running(self):
        return self.worker is not None and self.worker.is_alive()

    def _set_running(self, running, status=None):
        self.is_busy = running

        if status:
            self.status_var.set(status)
        elif not running:
            self.status_var.set("待命")

        self._refresh_action_buttons()

        if running:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _refresh_action_buttons(self):
        if self.is_busy:
            self.load_button.configure(state="disabled")
            self._configure_start_buttons(state="disabled")
            self.stop_button.configure(state="normal")
            return

        self.load_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

        if not self.book_info or not self.volumes:
            self._configure_start_buttons(text="请先加载目录", state="disabled")
        elif not self.checked_chapters:
            self._configure_start_buttons(text="请先选择章节", state="disabled")
        else:
            self._configure_start_buttons(
                text=f"开始爬取所选 {len(self.checked_chapters)} 章",
                state="normal",
            )

    def _configure_start_buttons(self, **options):
        for button in (self.start_button, getattr(self, "start_button_top", None)):
            if button is not None:
                button.configure(**options)

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "log":
                    tag = "error" if any(word in payload for word in ("失败", "错误", "Traceback", "Error")) else None
                    self._append_log(payload, tag)
                elif kind == "catalog_loaded":
                    self.book_info, self.volumes = payload
                    self._render_catalog()
                    self._set_running(False, "目录已加载")
                    self._append_log(
                        f"\n目录加载完成：{self.book_info['title']}，共 {len(self.volumes)} 卷。\n",
                        "success",
                    )
                elif kind == "crawl_done":
                    self._set_running(False, "完成")
                    self._append_log(f"\n爬取完成，生成 EPUB 数量：{len(payload)}\n", "success")
                elif kind == "error":
                    self._set_running(False, "失败")
                    self._append_log("\n" + payload + "\n", "error")
                    messagebox.showerror("任务失败", payload)
        except queue.Empty:
            pass

        self.after(100, self._poll_queue)

    def _update_selected_count(self):
        self.selected_count_var.set(f"已选择 {len(self.checked_chapters)} 章")
        self._refresh_action_buttons()

    def _append_log(self, text, tag=None):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text, tag)
        self.log_text.see("end")

    def clear_log(self):
        self.log_text.delete("1.0", "end")

    def open_downloads(self):
        try:
            output_dir = self._output_dir()
        except ValueError:
            output_dir = DOWNLOADS_DIR

        output_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(output_dir)

    def on_close(self):
        if self.is_busy:
            if not messagebox.askyesno("任务仍在运行", "任务仍在运行，要请求停止并关闭窗口吗？"):
                return
            self.stop_requested = True
        self.destroy()


def main():
    app = CrawlerGui()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
