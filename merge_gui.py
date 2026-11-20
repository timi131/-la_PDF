#!/usr/bin/env python3
"""
Графический интерфейс для объединения PDF / изображений / TXT в один PDF.

Нужен файл merge_files.py в той же папке.
Установка:  pip install pypdf pillow
Необязательно (перетаскивание файлов мышью из проводника):  pip install tkinterdnd2
Запуск:     python merge_gui.py
"""
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk
from pypdf import PdfReader

from merge_files import IMAGE_EXT, SUPPORTED, load_reader, make_groups, merge, split

try:
    import pdf_edit as pe
except ImportError:  # нет PyMuPDF — редактор будет недоступен
    pe = None

NUM_POS = {"Снизу по центру": "bottom-center", "Снизу справа": "bottom-right",
           "Снизу слева": "bottom-left", "Сверху по центру": "top-center",
           "Сверху справа": "top-right", "Сверху слева": "top-left"}

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

# ----- цвета -----
BG = "#1e1f26"
PANEL = "#272935"
FG = "#e8e8f0"
MUTED = "#9a9cb0"
ACCENT = "#5b8def"
ACCENT_HOVER = "#7aa3f5"
GREEN = "#3fb37f"
GREEN_HOVER = "#55c892"
RED = "#e05d5d"


def human_size(n):
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"


def file_info(path):
    """Тип и число страниц файла."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            return "PDF", len(PdfReader(path).pages)
        if ext in IMAGE_EXT:
            with Image.open(path) as im:
                return ext[1:].upper(), getattr(im, "n_frames", 1)
        if ext == ".txt":
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = len(f.read().splitlines()) or 1
            return "TXT", (lines + 54) // 55
    except Exception:
        return ext[1:].upper(), "ошибка"
    return ext[1:].upper(), "?"


class App:
    def __init__(self, root):
        self.root = root
        self.files = []
        self.preview_img = None
        self.drag_index = None
        self.out_dir = tk.StringVar(value=os.path.expanduser("~"))
        self.name_var = tk.StringVar(value="объединённый")
        self.status = tk.StringVar(value="Добавьте файлы, чтобы начать")

        root.title("PDF Объединитель")
        root.geometry("1000x740")
        root.minsize(900, 660)
        root.configure(bg=BG)
        self._style()
        self._build()
        self._build_split()
        self._build_editor()
        self._bind_keys()
        self._refresh()

    # ---------------- стили ----------------
    def _style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Panel.TLabel", background=PANEL, foreground=FG)
        s.configure("Muted.TLabel", background=BG, foreground=MUTED)
        s.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
        s.configure("Title.TLabel", background=BG, foreground=FG, font=("Segoe UI", 16, "bold"))
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                    rowheight=28, borderwidth=0)
        s.configure("Treeview.Heading", background="#323545", foreground=MUTED,
                    relief="flat", font=("Segoe UI", 9, "bold"))
        s.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
        s.map("Treeview.Heading", background=[("active", "#3a3d50")])
        s.configure("TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG,
                    bordercolor="#3a3d50", lightcolor="#3a3d50", darkcolor="#3a3d50", padding=6)
        s.configure("Horizontal.TProgressbar", background=GREEN, troughcolor=PANEL,
                    bordercolor=PANEL, lightcolor=GREEN, darkcolor=GREEN)
        for name, base, hover in (("Accent", ACCENT, ACCENT_HOVER), ("Go", GREEN, GREEN_HOVER),
                                  ("Tool", "#323545", "#3e4257"), ("Danger", "#323545", RED)):
            s.configure(f"{name}.TButton", background=base, foreground="white", borderwidth=0,
                        focusthickness=0, padding=(12, 7), font=("Segoe UI", 10, "bold"
                        if name in ("Accent", "Go") else "normal"))
            s.map(f"{name}.TButton", background=[("active", hover), ("disabled", "#2c2e3a")],
                  foreground=[("disabled", MUTED)])
        s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(16, 4, 16, 0))
        s.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(18, 8),
                    borderwidth=0, font=("Segoe UI", 10, "bold"))
        s.map("TNotebook.Tab", background=[("selected", ACCENT)], foreground=[("selected", "white")],
              expand=[("selected", (0, 0, 0, 0))])
        s.configure("TRadiobutton", background=PANEL, foreground=FG, indicatorbackground=BG,
                    indicatorforeground=ACCENT, font=("Segoe UI", 10))
        s.map("TRadiobutton", background=[("active", PANEL)],
              indicatorbackground=[("selected", ACCENT)])
        for name in ("Accent", "Go", "Tool", "Danger"):
            s.configure(f"{name}.TButton", width=-2, padding=(10, 7))
        s.configure("TCombobox", fieldbackground=PANEL, background="#323545", foreground=FG,
                    arrowcolor=FG, bordercolor="#3a3d50", lightcolor="#3a3d50", darkcolor="#3a3d50",
                    selectbackground=PANEL, selectforeground=FG, padding=4)
        s.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)],
              selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", FG)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        s.configure("Horizontal.TScale", background=ACCENT, troughcolor=BG, bordercolor=PANEL,
                    lightcolor=ACCENT, darkcolor=ACCENT)
        s.configure("Big.Go.TButton", font=("Segoe UI", 12, "bold"), padding=(20, 10))

    # ---------------- разметка ----------------
    def _build(self):
        r = self.root
        header = ttk.Frame(r, padding=(16, 14, 16, 4))
        header.pack(fill=tk.X)
        ttk.Label(header, text="📄  PDF Объединитель", style="Title.TLabel").pack(side=tk.LEFT)
        hint = "Перетащите файлы в список" if HAS_DND else "PDF · JPG · PNG · TIFF · WEBP · TXT"
        ttk.Label(header, text=hint, style="Muted.TLabel").pack(side=tk.RIGHT)

        self.nb = ttk.Notebook(r)
        self.nb.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        r = ttk.Frame(self.nb)
        self.split_tab = ttk.Frame(self.nb)
        self.nb.add(r, text="  🔗  Объединить  ")
        self.nb.add(self.split_tab, text="  ✂  Разделить  ")
        self.edit_tab = ttk.Frame(self.nb)
        self.nb.add(self.edit_tab, text="  ✏  Редактор  ")

        # панель инструментов
        tb = ttk.Frame(r, padding=(16, 10, 16, 6))
        tb.pack(fill=tk.X)
        ttk.Button(tb, text="＋ Добавить файлы", style="Accent.TButton",
                   command=self.add_files).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(tb, text="📁 Добавить папку", style="Tool.TButton",
                   command=self.add_folder).pack(side=tk.LEFT, padx=(0, 12))
        self.btn_up = ttk.Button(tb, text="▲", width=3, style="Tool.TButton", command=lambda: self.move(-1))
        self.btn_down = ttk.Button(tb, text="▼", width=3, style="Tool.TButton", command=lambda: self.move(1))
        self.btn_up.pack(side=tk.LEFT, padx=2)
        self.btn_down.pack(side=tk.LEFT, padx=2)
        ttk.Button(tb, text="А→Я", style="Tool.TButton", command=self.sort_by_name).pack(side=tk.LEFT, padx=(2, 12))
        self.btn_del = ttk.Button(tb, text="✕ Удалить", style="Danger.TButton", command=self.remove)
        self.btn_del.pack(side=tk.LEFT, padx=2)
        ttk.Button(tb, text="Очистить", style="Danger.TButton", command=self.clear).pack(side=tk.LEFT, padx=2)

        # центр: список + превью
        body = ttk.Frame(r, padding=(16, 6))
        body.pack(fill=tk.BOTH, expand=True)

        list_frame = ttk.Frame(body, style="Panel.TFrame")
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cols = ("n", "name", "type", "pages", "size")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", selectmode="extended")
        for c, title, w, anchor in (("n", "№", 40, "center"), ("name", "Файл", 300, "w"),
                                    ("type", "Тип", 60, "center"), ("pages", "Стр.", 55, "center"),
                                    ("size", "Размер", 80, "e")):
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor=anchor, stretch=(c == "name"))
        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.empty_label = tk.Label(list_frame, text="Список пуст\n\nНажмите «＋ Добавить файлы»"
                                    + ("\nили перетащите их сюда" if HAS_DND else ""),
                                    bg=PANEL, fg=MUTED, font=("Segoe UI", 11), justify="center")

        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.bind("<ButtonPress-1>", self._drag_start)
        self.tree.bind("<B1-Motion>", self._drag_motion)
        self.tree.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag_index", None))
        self.tree.bind("<Double-1>", lambda e: self.open_selected())

        if HAS_DND:
            for w in (self.tree, self.empty_label):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)

        # превью
        prev = ttk.Frame(body, style="Panel.TFrame", padding=10, width=240)
        prev.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        prev.pack_propagate(False)
        ttk.Label(prev, text="Предпросмотр", style="PanelMuted.TLabel").pack(anchor="w")
        self.preview = tk.Label(prev, bg=PANEL, fg=MUTED, text="—")
        self.preview.pack(fill=tk.BOTH, expand=True, pady=8)
        self.preview_info = ttk.Label(prev, text="", style="PanelMuted.TLabel", wraplength=220)
        self.preview_info.pack(anchor="w")

        # низ: имя, папка, кнопка
        bottom = ttk.Frame(r, padding=(16, 8, 16, 4))
        bottom.pack(fill=tk.X)
        bottom.columnconfigure(1, weight=1)

        ttk.Label(bottom, text="Имя файла:").grid(row=0, column=0, sticky="w", pady=4)
        name_box = ttk.Frame(bottom)
        name_box.grid(row=0, column=1, sticky="ew", padx=8)
        self.name_entry = ttk.Entry(name_box, textvariable=self.name_var, font=("Segoe UI", 11))
        self.name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(name_box, text=".pdf", style="Muted.TLabel").pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(bottom, text="Сохранить в:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(bottom, textvariable=self.out_dir).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(bottom, text="Обзор…", style="Tool.TButton",
                   command=self.choose_dir).grid(row=1, column=2, sticky="ew")

        self.btn_go = ttk.Button(bottom, text="Объединить в PDF", style="Big.Go.TButton",
                                 command=self.start_merge)
        self.btn_go.grid(row=0, column=3, rowspan=2, sticky="nsew", padx=(14, 0))

        self.open_after = tk.BooleanVar(value=True)
        tk.Checkbutton(bottom, text="Открыть файл после создания", variable=self.open_after,
                       bg=BG, fg=MUTED, selectcolor=PANEL, activebackground=BG,
                       activeforeground=FG, bd=0, highlightthickness=0).grid(
            row=2, column=1, sticky="w", padx=4, pady=(4, 0))

        # статус
        st = ttk.Frame(r, padding=(16, 4, 16, 12))
        st.pack(fill=tk.X)
        self.progress = ttk.Progressbar(st, mode="determinate", length=200)
        self.progress.pack(side=tk.RIGHT)
        ttk.Label(st, textvariable=self.status, style="Muted.TLabel").pack(side=tk.LEFT)

    def _bind_keys(self):
        self.root.bind("<Delete>", lambda e: self.nb.index("current") == 0 and self.remove())
        self.root.bind_all("<Key>", self.ed_key, add="+")
        self.root.bind("<Control-o>", lambda e: self.add_files())
        self.root.bind("<Alt-Up>", lambda e: self.move(-1))
        self.root.bind("<Alt-Down>", lambda e: self.move(1))
        self.root.bind("<Control-Return>", lambda e: [self.start_merge, self.start_split,
                                                       self.ed_save][self.nb.index("current")]())

    # ---------------- работа со списком ----------------
    def _add(self, paths):
        added, skipped = 0, 0
        for p in paths:
            if os.path.isdir(p):
                self._add(sorted(os.path.join(p, f) for f in os.listdir(p)))
                continue
            if os.path.splitext(p)[1].lower() not in SUPPORTED:
                skipped += 1
                continue
            typ, pages = file_info(p)
            self.files.append({"path": p, "type": typ, "pages": pages, "size": os.path.getsize(p)})
            added += 1
        self._refresh(select=len(self.files) - 1 if added else None)
        if skipped:
            self.status.set(f"Пропущено неподдерживаемых файлов: {skipped}")

    def add_files(self):
        pats = " ".join("*" + e for e in sorted(SUPPORTED))
        paths = filedialog.askopenfilenames(title="Выберите файлы",
                                            filetypes=[("Поддерживаемые", pats), ("Все файлы", "*.*")])
        if paths:
            self._add(paths)

    def add_folder(self):
        d = filedialog.askdirectory(title="Выберите папку")
        if d:
            self._add([d])

    def _on_drop(self, event):
        self._add(self.root.tk.splitlist(event.data))

    def _selected(self):
        return sorted(self.tree.index(i) for i in self.tree.selection())

    def remove(self):
        sel = self._selected()
        for i in reversed(sel):
            self.files.pop(i)
        self._refresh(select=min(sel[0], len(self.files) - 1) if sel else None)

    def clear(self):
        if self.files and messagebox.askyesno("Очистить", "Удалить все файлы из списка?"):
            self.files.clear()
            self._refresh()

    def move(self, d):
        sel = self._selected()
        if len(sel) != 1:
            return
        i, j = sel[0], sel[0] + d
        if 0 <= j < len(self.files):
            self.files[i], self.files[j] = self.files[j], self.files[i]
            self._refresh(select=j)

    def sort_by_name(self):
        self.files.sort(key=lambda f: os.path.basename(f["path"]).lower())
        self._refresh()

    def _drag_start(self, e):
        row = self.tree.identify_row(e.y)
        self.drag_index = self.tree.index(row) if row else None

    def _drag_motion(self, e):
        if self.drag_index is None:
            return
        row = self.tree.identify_row(e.y)
        if not row:
            return
        target = self.tree.index(row)
        if target != self.drag_index:
            self.files.insert(target, self.files.pop(self.drag_index))
            self.drag_index = target
            self._refresh(select=target)

    def _refresh(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for n, f in enumerate(self.files, 1):
            self.tree.insert("", tk.END, values=(n, os.path.basename(f["path"]), f["type"],
                                                 f["pages"], human_size(f["size"])))
        if self.files:
            self.empty_label.place_forget()
            total = sum(f["pages"] for f in self.files if isinstance(f["pages"], int))
            self.status.set(f"Файлов: {len(self.files)}   ·   страниц в итоге: ≈{total}")
        else:
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")
            self.status.set("Добавьте файлы, чтобы начать")
        if select is not None and 0 <= select < len(self.files):
            item = self.tree.get_children()[select]
            self.tree.selection_set(item)
            self.tree.see(item)
        self._on_select()

    # ---------------- превью ----------------
    def _on_select(self):
        sel = self._selected()
        has = bool(sel)
        for b in (self.btn_up, self.btn_down, self.btn_del):
            b.state(["!disabled"] if has else ["disabled"])
        self.btn_go.state(["!disabled"] if self.files else ["disabled"])
        if not has:
            self.preview.configure(image="", text="—")
            self.preview_info.configure(text="")
            return
        f = self.files[sel[0]]
        self.preview_info.configure(text=f"{os.path.basename(f['path'])}\n{f['type']} · "
                                    f"стр.: {f['pages']} · {human_size(f['size'])}")
        thumb = self._thumbnail(f["path"])
        if thumb:
            self.preview_img = ImageTk.PhotoImage(thumb)
            self.preview.configure(image=self.preview_img, text="")
        else:
            self.preview.configure(image="", text={"PDF": "📕\nPDF", "TXT": "📝\nТекст"}
                                   .get(f["type"], "Нет превью"), font=("Segoe UI", 20))

    def _thumbnail(self, path):
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in IMAGE_EXT:
                im = Image.open(path)
                im.seek(0)
                im = im.convert("RGB")
            elif ext == ".pdf":
                # превью PDF — если установлен PyMuPDF (pip install pymupdf)
                try:
                    import pymupdf as fitz
                except ImportError:
                    import fitz
                with fitz.open(path) as doc:
                    pix = doc[0].get_pixmap(dpi=50)
                    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            else:
                return None
            im.thumbnail((220, 300))
            return im
        except Exception:
            return None

    def open_selected(self):
        sel = self._selected()
        if sel:
            open_path(self.files[sel[0]]["path"])

    # ---------------- объединение ----------------
    def choose_dir(self):
        d = filedialog.askdirectory(title="Куда сохранить?", initialdir=self.out_dir.get())
        if d:
            self.out_dir.set(d)

    def start_merge(self):
        if not self.files:
            return
        name = self.name_var.get().strip() or "объединённый"
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        if name.lower().endswith(".pdf"):
            name = name[:-4]
        folder = self.out_dir.get().strip()
        if not os.path.isdir(folder):
            messagebox.showerror("Ошибка", "Папка для сохранения не существует.")
            return
        out = os.path.join(folder, name + ".pdf")
        if os.path.exists(out) and not messagebox.askyesno(
                "Файл существует", f"{out}\nуже существует. Перезаписать?"):
            return

        paths = [f["path"] for f in self.files]
        self.btn_go.state(["disabled"])
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status.set("Объединение…")

        def work():
            try:
                result = merge(paths, out)
                self.root.after(0, self._done, result, None)
            except Exception as e:
                self.root.after(0, self._done, None, e)

        threading.Thread(target=work, daemon=True).start()

    def _done(self, result, error):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100 if result else 0)
        self.btn_go.state(["!disabled"])
        if error:
            self.status.set("Ошибка")
            messagebox.showerror("Ошибка", str(error))
            return
        self.status.set(f"✔ Сохранено: {result}")
        if self.open_after.get():
            open_path(result)
        else:
            messagebox.showinfo("Готово", f"Файл сохранён:\n{result}")

    # ================= вкладка «Разделить» =================
    def _build_split(self):
        t = self.split_tab
        self.sp_path = tk.StringVar()
        self.sp_total = 0
        self.sp_mode = tk.StringVar(value="each")
        self.sp_every = tk.StringVar(value="2")
        self.sp_ranges = tk.StringVar(value="1-2, 3-")
        self.sp_extract = tk.StringVar(value="1, 3-")
        self.sp_prefix = tk.StringVar()
        self.sp_dir = tk.StringVar(value=os.path.expanduser("~"))
        self.sp_fmt = tk.StringVar(value="pdf")
        self.sp_status = tk.StringVar(value="Выберите файл для разделения")
        self.sp_open = tk.BooleanVar(value=True)
        self.sp_preview_img = None

        top = ttk.Frame(t, padding=(16, 12, 16, 6))
        top.pack(fill=tk.X)
        ttk.Button(top, text="📂 Выбрать файл", style="Accent.TButton",
                   command=self.sp_choose).pack(side=tk.LEFT)
        self.sp_file_label = ttk.Label(top, text="Файл не выбран", style="Muted.TLabel")
        self.sp_file_label.pack(side=tk.LEFT, padx=12)

        body = ttk.Frame(t, padding=(16, 6))
        body.pack(fill=tk.BOTH, expand=True)

        opts = ttk.Frame(body, style="Panel.TFrame", padding=16)
        opts.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        opts.columnconfigure(1, weight=1)
        ttk.Label(opts, text="Как разделить", style="PanelMuted.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self.sp_entries = {}
        rows = [("each", "Каждая страница — отдельный файл", None, ""),
                ("every", "По N страниц в файле", self.sp_every, "N = "),
                ("ranges", "Каждый диапазон — отдельный файл", self.sp_ranges, ""),
                ("extract", "Извлечь выбранные страницы в один файл", self.sp_extract, "")]
        for i, (mode, text, var, lbl) in enumerate(rows, 1):
            ttk.Radiobutton(opts, text=text, value=mode, variable=self.sp_mode,
                            command=self._sp_update).grid(row=i * 2 - 1, column=0, columnspan=2,
                                                          sticky="w", pady=(6, 0))
            if var is not None:
                e = ttk.Entry(opts, textvariable=var, width=8 if mode == "every" else 30)
                e.grid(row=i * 2, column=0, columnspan=2, sticky="w" if mode == "every" else "ew",
                       padx=(26, 0), pady=(2, 0))
                var.trace_add("write", lambda *a: self._sp_update())
                self.sp_entries[mode] = e
        ttk.Label(opts, text="Диапазоны: «1-3, 5, 8-» (8- значит с 8-й до конца)",
                  style="PanelMuted.TLabel").grid(row=9, column=0, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Label(opts, text="Формат результата", style="PanelMuted.TLabel").grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(16, 4))
        fmt_box = ttk.Frame(opts, style="Panel.TFrame")
        fmt_box.grid(row=11, column=0, columnspan=2, sticky="w")
        try:
            try:
                import pymupdf  # noqa: F401
            except ImportError:
                import fitz  # noqa: F401
            has_fitz = True
        except ImportError:
            has_fitz = False
        for val, text in (("pdf", "PDF"), ("jpg", "JPG"), ("png", "PNG")):
            rb = ttk.Radiobutton(fmt_box, text=text, value=val, variable=self.sp_fmt,
                                 command=self._sp_update)
            rb.pack(side=tk.LEFT, padx=(0, 14))
            if val != "pdf" and not has_fitz:
                rb.state(["disabled"])
        if not has_fitz:
            ttk.Label(opts, text="JPG/PNG: нужно pip install pymupdf",
                      style="PanelMuted.TLabel").grid(row=12, column=0, columnspan=2, sticky="w")

        # справа — превью и итог
        right = ttk.Frame(body, style="Panel.TFrame", padding=10, width=240)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        right.pack_propagate(False)
        ttk.Label(right, text="Файл", style="PanelMuted.TLabel").pack(anchor="w")
        self.sp_preview = tk.Label(right, bg=PANEL, fg=MUTED, text="—", font=("Segoe UI", 20))
        self.sp_preview.pack(fill=tk.BOTH, expand=True, pady=8)
        self.sp_result = ttk.Label(right, text="", style="Panel.TLabel", wraplength=220,
                                   font=("Segoe UI", 10, "bold"))
        self.sp_result.pack(anchor="w")

        bottom = ttk.Frame(t, padding=(16, 8, 16, 4))
        bottom.pack(fill=tk.X)
        bottom.columnconfigure(1, weight=1)
        ttk.Label(bottom, text="Начало имени:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(bottom, textvariable=self.sp_prefix, font=("Segoe UI", 11)).grid(
            row=0, column=1, sticky="ew", padx=8)
        ttk.Label(bottom, text="Сохранить в:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(bottom, textvariable=self.sp_dir).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(bottom, text="Обзор…", style="Tool.TButton", command=lambda: self._pick_dir(
            self.sp_dir)).grid(row=1, column=2, sticky="ew")
        self.sp_go = ttk.Button(bottom, text="Разделить", style="Big.Go.TButton",
                                command=self.start_split)
        self.sp_go.grid(row=0, column=3, rowspan=2, sticky="nsew", padx=(14, 0))
        tk.Checkbutton(bottom, text="Открыть папку после создания", variable=self.sp_open,
                       bg=BG, fg=MUTED, selectcolor=PANEL, activebackground=BG,
                       activeforeground=FG, bd=0, highlightthickness=0).grid(
            row=2, column=1, sticky="w", padx=4, pady=(4, 0))

        st = ttk.Frame(t, padding=(16, 4, 16, 12))
        st.pack(fill=tk.X)
        self.sp_progress = ttk.Progressbar(st, mode="determinate", length=200)
        self.sp_progress.pack(side=tk.RIGHT)
        ttk.Label(st, textvariable=self.sp_status, style="Muted.TLabel").pack(side=tk.LEFT)

        if HAS_DND:
            for w in (self.sp_preview, opts):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", lambda e: self.sp_load(self.root.tk.splitlist(e.data)[0]))
        self._sp_update()

    def _pick_dir(self, var):
        d = filedialog.askdirectory(title="Куда сохранить?", initialdir=var.get())
        if d:
            var.set(d)

    def sp_choose(self):
        pats = " ".join("*" + e for e in sorted(SUPPORTED))
        p = filedialog.askopenfilename(title="Файл для разделения",
                                       filetypes=[("Поддерживаемые", pats), ("Все файлы", "*.*")])
        if p:
            self.sp_load(p)

    def sp_load(self, path):
        try:
            self.sp_total = len(load_reader(path).pages)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось открыть файл:\n{e}")
            return
        self.sp_path.set(path)
        self.sp_prefix.set(os.path.splitext(os.path.basename(path))[0])
        self.sp_dir.set(os.path.dirname(path))
        self.sp_status.set("Выберите способ разделения и нажмите «Разделить»")
        self.sp_file_label.configure(
            text=f"{os.path.basename(path)}   ·   страниц: {self.sp_total}   ·   "
                 f"{human_size(os.path.getsize(path))}", style="TLabel")
        thumb = self._thumbnail(path)
        if thumb:
            self.sp_preview_img = ImageTk.PhotoImage(thumb)
            self.sp_preview.configure(image=self.sp_preview_img, text="")
        else:
            self.sp_preview.configure(image="", text="📕\nPDF")
        self._sp_update()

    def _sp_groups(self):
        mode = self.sp_mode.get()
        val = {"every": self.sp_every, "ranges": self.sp_ranges,
               "extract": self.sp_extract}.get(mode)
        return make_groups(self.sp_total, mode, val.get() if val else "")

    def _sp_update(self):
        mode = self.sp_mode.get()
        for m, e in self.sp_entries.items():
            e.state(["!disabled"] if m == mode else ["disabled"])
        if not self.sp_total:
            self.sp_go.state(["disabled"])
            self.sp_result.configure(text="")
            return
        try:
            groups = self._sp_groups()
        except Exception as e:
            self.sp_result.configure(text=f"⚠ {e}", foreground=RED)
            self.sp_go.state(["disabled"])
            return
        pages = sum(len(g) for g in groups)
        n = pages if self.sp_fmt.get() != "pdf" else len(groups)
        self.sp_result.configure(text=f"Будет создано файлов: {n}\nСтраниц всего: {pages}",
                                 foreground=GREEN)
        self.sp_go.state(["!disabled"])

    def start_split(self):
        if not self.sp_total:
            return
        try:
            groups = self._sp_groups()
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))
            return
        prefix = self.sp_prefix.get().strip() or "страницы"
        for ch in '<>:"/\\|?*':
            prefix = prefix.replace(ch, "_")
        out_dir = self.sp_dir.get().strip()
        if not out_dir:
            messagebox.showerror("Ошибка", "Укажите папку для сохранения.")
            return
        path, fmt = self.sp_path.get(), self.sp_fmt.get()
        self.sp_go.state(["disabled"])
        self.sp_progress.configure(value=0, maximum=len(groups))
        self.sp_status.set("Разделение…")

        def prog(k, total):
            self.root.after(0, lambda: (self.sp_progress.configure(value=k),
                                        self.sp_status.set(f"Разделение… {k}/{total}")))

        def work():
            try:
                res = split(path, out_dir, groups, prefix, fmt, progress=prog)
                self.root.after(0, self._sp_done, res, out_dir, None)
            except Exception as e:
                self.root.after(0, self._sp_done, None, out_dir, e)

        threading.Thread(target=work, daemon=True).start()

    def _sp_done(self, res, out_dir, error):
        self.sp_go.state(["!disabled"])
        if error:
            self.sp_status.set("Ошибка")
            messagebox.showerror("Ошибка", str(error))
            return
        self.sp_status.set(f"✔ Создано файлов: {len(res)} в {out_dir}")
        if self.sp_open.get():
            open_path(out_dir)
        else:
            messagebox.showinfo("Готово", f"Создано файлов: {len(res)}\nПапка: {out_dir}")


    # ================= вкладка «Редактор» =================
    def _build_editor(self):
        t = self.edit_tab
        self.ed_docs = []
        self.ed_pages = []
        self.ed_sel = set()
        self.ed_anchor = None
        self.ed_undo = []
        self.ed_thumbs = {}
        self.ed_cells = []
        self.ed_drag = None
        self.ed_src_name = "документ"

        if pe is None:
            ttk.Label(t, text="Для редактора нужна библиотека PyMuPDF:\n\npip install pymupdf\n\n"
                      "Установите её и перезапустите программу.", style="Muted.TLabel",
                      justify="center", font=("Segoe UI", 12)).pack(expand=True)
            return

        tb = ttk.Frame(t, padding=(16, 10, 16, 6))
        tb.pack(fill=tk.X)

        def btn(text, cmd, style="Tool.TButton", **kw):
            b = ttk.Button(tb, text=text, style=style, command=cmd, **kw)
            b.pack(side=tk.LEFT, padx=2)
            return b

        btn("📂 Открыть", self.ed_open, "Accent.TButton")
        btn("＋ Страницы из файла", self.ed_insert_file)
        ttk.Frame(tb, width=10).pack(side=tk.LEFT)
        self.ed_sel_btns = [
            btn("⟲ 90°", lambda: self.ed_rotate(-90)),
            btn("⟳ 90°", lambda: self.ed_rotate(90)),
            btn("180°", lambda: self.ed_rotate(180)),
        ]
        ttk.Frame(tb, width=10).pack(side=tk.LEFT)
        self.ed_sel_btns += [
            btn("◀", lambda: self.ed_move(-1), width=3),
            btn("▶", lambda: self.ed_move(1), width=3),
            btn("⧉ Копия", self.ed_duplicate),
            btn("✕ Удалить", self.ed_delete, "Danger.TButton"),
        ]
        btn("□ Пустая", self.ed_blank)
        self.ed_undo_btn = btn("↶ Отменить", self.ed_undo_last)

        tb2 = ttk.Frame(t, padding=(16, 0, 16, 4))
        tb2.pack(fill=tk.X)
        for text, cmd in (("Выделить все", self.ed_select_all), ("Чётные", lambda: self.ed_select_parity(0)),
                          ("Нечётные", lambda: self.ed_select_parity(1)),
                          ("Снять выделение", lambda: self.ed_set_sel(set())),
                          ("⇅ Обратный порядок", self.ed_reverse)):
            ttk.Button(tb2, text=text, style="Tool.TButton", command=cmd).pack(side=tk.LEFT, padx=2)
        ttk.Label(tb2, text="Ctrl/Shift+клик · перетаскивание",
                  style="Muted.TLabel").pack(side=tk.RIGHT)

        body = ttk.Frame(t, padding=(16, 6))
        body.pack(fill=tk.BOTH, expand=True)

        grid_frame = ttk.Frame(body, style="Panel.TFrame")
        grid_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.ed_canvas = tk.Canvas(grid_frame, bg=PANEL, highlightthickness=0)
        sb = ttk.Scrollbar(grid_frame, orient=tk.VERTICAL, command=self.ed_canvas.yview)
        self.ed_canvas.configure(yscrollcommand=sb.set)
        self.ed_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        c = self.ed_canvas
        c.bind("<Configure>", lambda e: self.ed_redraw())
        c.bind("<ButtonPress-1>", self.ed_click)
        c.bind("<B1-Motion>", self.ed_motion)
        c.bind("<ButtonRelease-1>", self.ed_release)
        c.bind("<Double-1>", self.ed_dblclick)
        c.bind("<MouseWheel>", lambda e: c.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units"))
        c.bind("<Button-4>", lambda e: c.yview_scroll(-1, "units"))
        c.bind("<Button-5>", lambda e: c.yview_scroll(1, "units"))
        if HAS_DND:
            c.drop_target_register(DND_FILES)
            c.dnd_bind("<<Drop>>", lambda e: self.ed_add_paths(self.root.tk.splitlist(e.data)))

        # правая панель — оформление при сохранении
        opt = ttk.Frame(body, style="Panel.TFrame", padding=12, width=250)
        opt.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        opt.pack_propagate(False)

        def chk(text, var):
            tk.Checkbutton(opt, text=text, variable=var, bg=PANEL, fg=FG, selectcolor=BG,
                           activebackground=PANEL, activeforeground=FG, bd=0, highlightthickness=0,
                           font=("Segoe UI", 10), anchor="w").pack(fill=tk.X, pady=(10, 2))

        ttk.Label(opt, text="При сохранении", style="PanelMuted.TLabel").pack(anchor="w")
        self.ed_wm_on = tk.BooleanVar(value=False)
        self.ed_wm_text = tk.StringVar(value="ЧЕРНОВИК")
        self.ed_wm_op = tk.IntVar(value=20)
        chk("Водяной знак", self.ed_wm_on)
        ttk.Entry(opt, textvariable=self.ed_wm_text).pack(fill=tk.X)
        op_row = ttk.Frame(opt, style="Panel.TFrame")
        op_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(op_row, text="Прозрачность", style="PanelMuted.TLabel").pack(side=tk.LEFT)
        self.ed_op_lbl = ttk.Label(op_row, text="20%", style="PanelMuted.TLabel", width=5)
        self.ed_op_lbl.pack(side=tk.RIGHT)
        ttk.Scale(opt, from_=5, to=100, variable=self.ed_wm_op, command=lambda v:
                  self.ed_op_lbl.configure(text=f"{int(float(v))}%")).pack(fill=tk.X)

        self.ed_num_on = tk.BooleanVar(value=False)
        self.ed_num_fmt = tk.StringVar(value="Стр. {n} из {total}")
        self.ed_num_pos = tk.StringVar(value="Снизу по центру")
        chk("Номера страниц", self.ed_num_on)
        ttk.Combobox(opt, textvariable=self.ed_num_fmt, values=["{n}", "{n} / {total}", "Стр. {n} из {total}",
                                                               "- {n} -"]).pack(fill=tk.X)
        ttk.Combobox(opt, textvariable=self.ed_num_pos, state="readonly",
                     values=list(NUM_POS)).pack(fill=tk.X, pady=(4, 0))

        self.ed_pw_on = tk.BooleanVar(value=False)
        self.ed_pw = tk.StringVar()
        chk("Пароль на открытие", self.ed_pw_on)
        ttk.Entry(opt, textvariable=self.ed_pw, show="•").pack(fill=tk.X)

        self.ed_compress = tk.BooleanVar(value=True)
        chk("Сжать файл", self.ed_compress)

        self.ed_info = ttk.Label(opt, text="", style="Panel.TLabel", wraplength=225,
                                 font=("Segoe UI", 10, "bold"))
        self.ed_info.pack(side=tk.BOTTOM, anchor="w")

        bottom = ttk.Frame(t, padding=(16, 8, 16, 4))
        bottom.pack(fill=tk.X)
        bottom.columnconfigure(1, weight=1)
        self.ed_name = tk.StringVar(value="изменённый")
        self.ed_dir = tk.StringVar(value=os.path.expanduser("~"))
        ttk.Label(bottom, text="Имя файла:").grid(row=0, column=0, sticky="w", pady=4)
        nb = ttk.Frame(bottom)
        nb.grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Entry(nb, textvariable=self.ed_name, font=("Segoe UI", 11)).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(nb, text=".pdf", style="Muted.TLabel").pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(bottom, text="Сохранить в:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(bottom, textvariable=self.ed_dir).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(bottom, text="Обзор…", style="Tool.TButton",
                   command=lambda: self._pick_dir(self.ed_dir)).grid(row=1, column=2, sticky="ew")
        self.ed_save_btn = ttk.Button(bottom, text="Сохранить PDF", style="Big.Go.TButton",
                                      command=self.ed_save)
        self.ed_save_btn.grid(row=0, column=3, rowspan=2, sticky="nsew", padx=(14, 0))
        self.ed_open_after = tk.BooleanVar(value=True)
        tk.Checkbutton(bottom, text="Открыть файл после сохранения", variable=self.ed_open_after,
                       bg=BG, fg=MUTED, selectcolor=PANEL, activebackground=BG,
                       activeforeground=FG, bd=0, highlightthickness=0).grid(
            row=2, column=1, sticky="w", padx=4, pady=(4, 0))

        st = ttk.Frame(t, padding=(16, 4, 16, 12))
        st.pack(fill=tk.X)
        self.ed_status = tk.StringVar(value="Откройте PDF или изображения, чтобы начать")
        ttk.Label(st, textvariable=self.ed_status, style="Muted.TLabel").pack(side=tk.LEFT)
        self.ed_update_ui()

    # ----- загрузка -----
    def _ed_pick(self, title):
        pats = " ".join("*" + e for e in sorted(SUPPORTED))
        return filedialog.askopenfilenames(title=title, filetypes=[("Поддерживаемые", pats),
                                                                   ("Все файлы", "*.*")])

    def ed_open(self):
        paths = self._ed_pick("Открыть для редактирования")
        if not paths:
            return
        if self.ed_pages and not messagebox.askyesno(
                "Открыть", "Закрыть текущий документ? Несохранённые изменения пропадут."):
            return
        for d in self.ed_docs:
            d.close()
        self.ed_docs, self.ed_pages, self.ed_undo, self.ed_thumbs = [], [], [], {}
        self.ed_sel = set()
        self.ed_src_name = os.path.splitext(os.path.basename(paths[0]))[0]
        self.ed_name.set(self.ed_src_name + "_изменён")
        self.ed_dir.set(os.path.dirname(paths[0]))
        self.ed_add_paths(paths, push=False)

    def ed_insert_file(self):
        paths = self._ed_pick("Добавить страницы из файла")
        if paths:
            pos = max(self.ed_sel) + 1 if self.ed_sel else len(self.ed_pages)
            self.ed_add_paths(paths, pos)

    def ed_add_paths(self, paths, pos=None, push=True):
        new = []
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            for p in paths:
                if os.path.splitext(p)[1].lower() not in SUPPORTED:
                    continue
                try:
                    doc = pe.open_doc(p)
                except Exception as e:
                    messagebox.showerror("Ошибка", f"{os.path.basename(p)}:\n{e}")
                    continue
                if doc.needs_pass:
                    pw = simpledialog.askstring("Пароль", f"Файл {os.path.basename(p)} защищён.\n"
                                                "Введите пароль:", show="•", parent=self.root)
                    if not pw or not doc.authenticate(pw):
                        messagebox.showerror("Ошибка", "Неверный пароль")
                        continue
                self.ed_docs.append(doc)
                new += pe.page_entries(len(self.ed_docs) - 1, doc)
        finally:
            self.root.configure(cursor="")
        if not new:
            return
        if push and self.ed_pages:
            self._ed_push()
        pos = len(self.ed_pages) if pos is None else pos
        self.ed_pages[pos:pos] = new
        self.ed_sel = set(range(pos, pos + len(new))) if self.ed_pages != new else set()
        self.ed_redraw()

    # ----- операции -----
    def _ed_push(self):
        self.ed_undo.append([dict(e) for e in self.ed_pages])
        del self.ed_undo[:-50]

    def ed_undo_last(self):
        if self.ed_undo:
            self.ed_pages = self.ed_undo.pop()
            self.ed_sel = {i for i in self.ed_sel if i < len(self.ed_pages)}
            self.ed_redraw()

    def _ed_targets(self):
        return sorted(self.ed_sel)

    def ed_rotate(self, angle):
        if not self.ed_sel:
            return
        self._ed_push()
        for i in self.ed_sel:
            self.ed_pages[i]["r"] = (self.ed_pages[i]["r"] + angle) % 360
        self.ed_redraw()

    def ed_delete(self):
        if not self.ed_sel:
            return
        self._ed_push()
        first = min(self.ed_sel)
        self.ed_pages = [e for i, e in enumerate(self.ed_pages) if i not in self.ed_sel]
        self.ed_sel = {min(first, len(self.ed_pages) - 1)} if self.ed_pages else set()
        self.ed_redraw()

    def ed_duplicate(self):
        if not self.ed_sel:
            return
        self._ed_push()
        sel = self._ed_targets()
        copies = [dict(self.ed_pages[i]) for i in sel]
        pos = sel[-1] + 1
        self.ed_pages[pos:pos] = copies
        self.ed_sel = set(range(pos, pos + len(copies)))
        self.ed_redraw()

    def ed_blank(self):
        self._ed_push()
        pos = max(self.ed_sel) + 1 if self.ed_sel else len(self.ed_pages)
        ref = self.ed_pages[pos - 1] if pos > 0 and self.ed_pages else None
        size = pe.page_size(self.ed_docs, ref) if ref else pe.A4
        self.ed_pages.insert(pos, {"d": None, "p": 0, "r": 0, "size": size})
        self.ed_sel = {pos}
        self.ed_redraw()

    def ed_move(self, d):
        sel = self._ed_targets()
        if not sel or (d < 0 and sel[0] == 0) or (d > 0 and sel[-1] == len(self.ed_pages) - 1):
            return
        self._ed_push()
        self._ed_move_block(sel, sel[0] + d if d < 0 else sel[0] + 1)

    def _ed_move_block(self, sel, target):
        """Переместить выделенные страницы так, чтобы первая оказалась на позиции target
        (позиция считается в списке без перемещаемых страниц)."""
        block = [self.ed_pages[i] for i in sel]
        rest = [e for i, e in enumerate(self.ed_pages) if i not in set(sel)]
        target = max(0, min(target, len(rest)))
        self.ed_pages = rest[:target] + block + rest[target:]
        self.ed_sel = set(range(target, target + len(block)))
        self.ed_redraw()

    def ed_reverse(self):
        if len(self.ed_pages) > 1:
            self._ed_push()
            self.ed_pages.reverse()
            n = len(self.ed_pages)
            self.ed_sel = {n - 1 - i for i in self.ed_sel}
            self.ed_redraw()

    def ed_set_sel(self, s):
        self.ed_sel = set(s)
        self.ed_redraw(keep_scroll=True)

    def ed_select_all(self):
        self.ed_set_sel(range(len(self.ed_pages)))

    def ed_select_parity(self, odd):
        # «Нечётные» — страницы 1, 3, 5… (индексы 0, 2, 4…)
        self.ed_set_sel(i for i in range(len(self.ed_pages)) if i % 2 == (0 if odd else 1))

    # ----- сетка миниатюр -----
    CELL_W, CELL_H, THUMB = 150, 190, 130

    def _ed_thumb(self, e):
        key = (e["d"], e["p"], e["r"], e.get("size"))
        if key not in self.ed_thumbs:
            self.ed_thumbs[key] = ImageTk.PhotoImage(pe.render_thumb(self.ed_docs, e, self.THUMB))
        return self.ed_thumbs[key]

    def ed_redraw(self, keep_scroll=True):
        c = self.ed_canvas
        y0 = c.yview()[0]
        c.delete("all")
        self.ed_cells = []
        width = max(c.winfo_width(), self.CELL_W)
        cols = max(1, width // self.CELL_W)
        pad = (width - cols * self.CELL_W) // 2
        if not self.ed_pages:
            c.create_text(width / 2, 120, fill=MUTED, font=("Segoe UI", 11), justify="center",
                          text="Нет страниц\n\nНажмите «📂 Открыть»" + (" или перетащите файлы сюда" if HAS_DND else ""))
        for i, e in enumerate(self.ed_pages):
            col, row = i % cols, i // cols
            x, y = pad + col * self.CELL_W, 10 + row * self.CELL_H
            self.ed_cells.append((x, y))
            img = self._ed_thumb(e)
            cx, cy = x + self.CELL_W / 2, y + 8 + self.THUMB / 2
            sel = i in self.ed_sel
            if sel:
                c.create_rectangle(x + 4, y, x + self.CELL_W - 4, y + self.CELL_H - 8,
                                   fill="#2f3f66", outline=ACCENT, width=2)
            c.create_image(cx, cy, image=img)
            label = str(i + 1)
            if e["r"]:
                label += f"  ↻{e['r']}°"
            if e["d"] is None:
                label += "  (пустая)"
            c.create_text(cx, y + self.THUMB + 28, text=label, fill="white" if sel else MUTED,
                          font=("Segoe UI", 9, "bold" if sel else "normal"))
        rows = (len(self.ed_pages) + cols - 1) // cols
        c.configure(scrollregion=(0, 0, width, max(rows * self.CELL_H + 20, c.winfo_height())))
        if keep_scroll:
            c.yview_moveto(y0)
        self.ed_cols, self.ed_pad = cols, pad
        self.ed_update_ui()

    def _ed_index_at(self, ev, nearest=False):
        x, y = self.ed_canvas.canvasx(ev.x), self.ed_canvas.canvasy(ev.y)
        col = int((x - self.ed_pad) // self.CELL_W)
        row = int((y - 10) // self.CELL_H)
        if nearest:
            col = max(0, min(col, self.ed_cols - 1))
            # куда вставлять: слева или справа от ячейки
            frac = (x - self.ed_pad) / self.CELL_W - col
            i = max(0, row) * self.ed_cols + col + (1 if frac > 0.5 else 0)
            return min(i, len(self.ed_pages))
        if not (0 <= col < self.ed_cols) or row < 0:
            return None
        i = row * self.ed_cols + col
        return i if i < len(self.ed_pages) else None

    def ed_click(self, ev):
        self.ed_canvas.focus_set()
        i = self._ed_index_at(ev)
        self.ed_drag = None
        if i is None:
            if not ev.state & 0x0005:
                self.ed_set_sel(set())
            return
        if ev.state & 0x0004:            # Ctrl
            self.ed_sel ^= {i}
            self.ed_anchor = i
        elif ev.state & 0x0001 and self.ed_anchor is not None:   # Shift
            a, b = sorted((self.ed_anchor, i))
            self.ed_sel = set(range(a, b + 1))
        else:
            if i not in self.ed_sel:
                self.ed_sel = {i}
            self.ed_anchor = i
            self.ed_drag = {"start": (ev.x, ev.y), "active": False, "index": i}
        self.ed_redraw()

    def ed_motion(self, ev):
        d = self.ed_drag
        if not d:
            return
        if not d["active"] and abs(ev.x - d["start"][0]) + abs(ev.y - d["start"][1]) < 8:
            return
        d["active"] = True
        c = self.ed_canvas
        c.delete("dropmark")
        i = self._ed_index_at(ev, nearest=True)
        k = min(i, len(self.ed_pages) - 1)
        x, y = self.ed_cells[k]
        if i == len(self.ed_pages) or (i > k):
            x += self.CELL_W
        c.create_line(x, y + 4, x, y + self.CELL_H - 12, fill=GREEN, width=4, tags="dropmark")
        d["target"] = i
        # автопрокрутка у краёв
        h = c.winfo_height()
        if ev.y < 20:
            c.yview_scroll(-1, "units")
        elif ev.y > h - 20:
            c.yview_scroll(1, "units")

    def ed_release(self, ev):
        d, self.ed_drag = self.ed_drag, None
        self.ed_canvas.delete("dropmark")
        if not d:
            return
        if not d["active"]:
            # обычный клик по уже выделенной странице — выделить только её
            if len(self.ed_sel) > 1:
                self.ed_set_sel({d["index"]})
            return
        sel = self._ed_targets()
        target = d.get("target")
        if target is None:
            return
        # позиция в списке без перемещаемых страниц
        target -= sum(1 for i in sel if i < target)
        if target == sel[0] and sel == list(range(sel[0], sel[-1] + 1)):
            return
        self._ed_push()
        self._ed_move_block(sel, target)

    def ed_dblclick(self, ev):
        i = self._ed_index_at(ev)
        if i is None:
            return
        e = self.ed_pages[i]
        win = tk.Toplevel(self.root)
        win.title(f"Страница {i + 1}")
        win.configure(bg=BG)
        img = pe.render_thumb(self.ed_docs, e, 800)
        ph = ImageTk.PhotoImage(img)
        lbl = tk.Label(win, image=ph, bg=BG)
        lbl.image = ph
        lbl.pack(padx=10, pady=10)
        win.bind("<Escape>", lambda _: win.destroy())

    def ed_update_ui(self):
        if pe is None:
            return
        has_sel = bool(self.ed_sel)
        for b in self.ed_sel_btns:
            b.state(["!disabled"] if has_sel else ["disabled"])
        self.ed_undo_btn.state(["!disabled"] if self.ed_undo else ["disabled"])
        self.ed_save_btn.state(["!disabled"] if self.ed_pages else ["disabled"])
        n = len(self.ed_pages)
        if n:
            rot = sum(1 for e in self.ed_pages if e["r"])
            self.ed_status.set(f"Страниц: {n}   ·   выделено: {len(self.ed_sel)}"
                               + (f"   ·   повёрнуто: {rot}" if rot else ""))
            self.ed_info.configure(text=f"Итоговый документ:\n{n} стр.", foreground=GREEN)
        else:
            self.ed_info.configure(text="")

    def ed_key(self, ev):
        """Горячие клавиши редактора."""
        if self.nb.index("current") != 2 or pe is None:
            return
        if isinstance(ev.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            return
        ctrl = ev.state & 0x0004
        k = ev.keysym.lower()
        if k == "delete":
            self.ed_delete()
        elif ctrl and k in ("z", "я"):
            self.ed_undo_last()
        elif ctrl and k in ("a", "ф"):
            self.ed_select_all()
        elif ctrl and k in ("d", "в"):
            self.ed_duplicate()
        elif k in ("r", "к"):
            self.ed_rotate(-90 if ev.state & 0x0001 else 90)
        elif k == "left" and self.ed_sel:
            self.ed_move(-1) if ev.state & 0x0008 else self.ed_set_sel({max(0, min(self.ed_sel) - 1)})
        elif k == "right" and self.ed_sel:
            self.ed_move(1) if ev.state & 0x0008 else self.ed_set_sel(
                {min(len(self.ed_pages) - 1, max(self.ed_sel) + 1)})
        else:
            return
        return "break"

    # ----- сохранение -----
    def ed_save(self):
        if not self.ed_pages:
            return
        name = self.ed_name.get().strip() or "изменённый"
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        if name.lower().endswith(".pdf"):
            name = name[:-4]
        folder = self.ed_dir.get().strip()
        if not os.path.isdir(folder):
            messagebox.showerror("Ошибка", "Папка для сохранения не существует.")
            return
        out = os.path.join(folder, name + ".pdf")
        if os.path.exists(out) and not messagebox.askyesno(
                "Файл существует", f"{out}\nуже существует. Перезаписать?"):
            return
        pw = self.ed_pw.get() if self.ed_pw_on.get() else None
        if self.ed_pw_on.get() and not pw:
            messagebox.showerror("Ошибка", "Введите пароль или снимите галочку «Пароль».")
            return
        self.root.configure(cursor="watch")
        self.ed_status.set("Сохранение…")
        self.root.update_idletasks()
        try:
            res = pe.build_pdf(
                self.ed_docs, self.ed_pages, out,
                watermark=self.ed_wm_text.get().strip() if self.ed_wm_on.get() else None,
                wm_opacity=self.ed_wm_op.get() / 100,
                numbers=self.ed_num_fmt.get() if self.ed_num_on.get() else None,
                number_pos=NUM_POS[self.ed_num_pos.get()],
                password=pw, compress=self.ed_compress.get())
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))
            self.ed_update_ui()
            return
        finally:
            self.root.configure(cursor="")
        self.ed_status.set(f"✔ Сохранено: {res}   ({human_size(os.path.getsize(res))})")
        if self.ed_open_after.get():
            open_path(res)
        else:
            messagebox.showinfo("Готово", f"Файл сохранён:\n{res}")


def open_path(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def main():
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    app = App(root)
    if len(sys.argv) > 1:          # можно передать файлы при запуске
        app._add(sys.argv[1:])
    root.mainloop()


if __name__ == "__main__":
    main()
