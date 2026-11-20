#!/usr/bin/env python3
"""
Объединение PDF, JPG, PNG и других изображений в один PDF-файл.

Установка зависимостей:
    pip install pypdf pillow

Запуск:
    python merge_files.py                      # графический интерфейс
    python merge_files.py a.pdf b.jpg -o итог   # объединить из командной строки
    python merge_files.py split doc.pdf                    # каждая страница отдельно
    python merge_files.py split doc.pdf --every 2          # по 2 страницы
    python merge_files.py split doc.pdf --ranges "1-3,5"   # каждый диапазон — файл
    python merge_files.py split doc.pdf --extract "1,4-6"  # выбранное — в один файл
"""
import argparse
import io
import os
import sys

from PIL import Image
from pypdf import PdfReader, PdfWriter

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
SUPPORTED = IMAGE_EXT | {".pdf", ".txt"}


def image_to_pdf_pages(path):
    """Превращает изображение (в т.ч. многостраничный TIFF/GIF) в PDF в памяти."""
    img = Image.open(path)
    frames = []
    try:
        while True:
            frame = img.copy()
            if frame.mode in ("RGBA", "LA", "P"):
                frame = frame.convert("RGBA")
                bg = Image.new("RGB", frame.size, (255, 255, 255))
                bg.paste(frame, mask=frame.split()[-1])
                frame = bg
            elif frame.mode != "RGB":
                frame = frame.convert("RGB")
            frames.append(frame)
            img.seek(img.tell() + 1)
    except EOFError:
        pass
    buf = io.BytesIO()
    frames[0].save(buf, "PDF", save_all=True, append_images=frames[1:], resolution=100)
    buf.seek(0)
    return PdfReader(buf)


def text_to_pdf_pages(path):
    """Простейшая конвертация .txt в PDF (через изображение, чтобы работала кириллица)."""
    from PIL import ImageDraw, ImageFont

    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines() or [""]
    font = None
    for name in ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf"):
        try:
            font = ImageFont.truetype(name, 22)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    w, h, margin, step = 1240, 1754, 80, 30  # A4 ~150 dpi
    per_page = (h - 2 * margin) // step
    pages = []
    for i in range(0, len(lines), per_page):
        page = Image.new("RGB", (w, h), "white")
        d = ImageDraw.Draw(page)
        for j, line in enumerate(lines[i:i + per_page]):
            d.text((margin, margin + j * step), line, fill="black", font=font)
        pages.append(page)
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], resolution=150)
    buf.seek(0)
    return PdfReader(buf)


def load_reader(path):
    """Открывает любой поддерживаемый файл как PDF."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return PdfReader(path)
    if ext in IMAGE_EXT:
        return image_to_pdf_pages(path)
    if ext == ".txt":
        return text_to_pdf_pages(path)
    raise ValueError(f"Неподдерживаемый формат: {path}")


def merge(files, output):
    """Объединяет файлы в один PDF. Возвращает путь к результату."""
    if not output.lower().endswith(".pdf"):
        output += ".pdf"
    writer = PdfWriter()
    for path in files:
        reader = load_reader(path)
        for page in reader.pages:
            writer.add_page(page)
    with open(output, "wb") as f:
        writer.write(f)
    return output


# ---------------------------- Разделение ----------------------------
def parse_ranges(text, total):
    """'1-3, 5, 8-' -> [[0,1,2], [4], [7..total-1]] (номера страниц с 1)."""
    groups = []
    for part in text.replace(";", ",").split(","):
        part = part.strip().replace(" ", "")
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a) if a else 1
            b = int(b) if b else total
        else:
            a = b = int(part)
        if not (1 <= a <= total and 1 <= b <= total):
            raise ValueError(f"Диапазон «{part}» выходит за пределы 1–{total}")
        step = 1 if b >= a else -1
        groups.append(list(range(a - 1, b - 1 + step, step)))
    if not groups:
        raise ValueError("Не указано ни одного диапазона")
    return groups


def make_groups(total, mode, value=""):
    """mode: 'each' — по странице; 'every' — по N страниц;
    'ranges' — каждый диапазон отдельным файлом; 'extract' — все диапазоны в один файл."""
    if mode == "each":
        return [[i] for i in range(total)]
    if mode == "every":
        n = int(value)
        if n < 1:
            raise ValueError("N должно быть больше 0")
        return [list(range(i, min(i + n, total))) for i in range(0, total, n)]
    if mode == "ranges":
        return parse_ranges(value, total)
    if mode == "extract":
        return [[i for g in parse_ranges(value, total) for i in g]]
    raise ValueError(f"Неизвестный режим: {mode}")


def _group_label(g):
    if len(g) == 1:
        return f"стр{g[0] + 1}"
    if g == list(range(g[0], g[-1] + 1)):
        return f"стр{g[0] + 1}-{g[-1] + 1}"
    return "выборка_стр" + ",".join(str(i + 1) for i in g)[:60]


def split(path, out_dir, groups, prefix=None, fmt="pdf", dpi=150, progress=None):
    """Сохраняет группы страниц в отдельные файлы. fmt: 'pdf', 'jpg' или 'png'
    (картинки требуют pip install pymupdf). Возвращает список созданных файлов."""
    os.makedirs(out_dir, exist_ok=True)
    prefix = prefix or os.path.splitext(os.path.basename(path))[0]
    reader = load_reader(path)
    outputs = []
    doc = None
    if fmt != "pdf":
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz  # PyMuPDF старых версий
        buf = io.BytesIO()
        w = PdfWriter()
        for pg in reader.pages:
            w.add_page(pg)
        w.write(buf)
        doc = fitz.open(stream=buf.getvalue(), filetype="pdf")
    for k, g in enumerate(groups, 1):
        label = _group_label(g)
        if fmt == "pdf":
            out = os.path.join(out_dir, f"{prefix}_{label}.pdf")
            w = PdfWriter()
            for i in g:
                w.add_page(reader.pages[i])
            with open(out, "wb") as f:
                w.write(f)
            outputs.append(out)
        else:
            for i in g:
                out = os.path.join(out_dir, f"{prefix}_стр{i + 1}.{fmt}")
                doc[i].get_pixmap(dpi=dpi).save(out)
                outputs.append(out)
        if progress:
            progress(k, len(groups))
    if doc:
        doc.close()
    return outputs


# ---------------------------- Графический интерфейс ----------------------------
def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.title("Объединение файлов в PDF")
    root.geometry("620x440")

    files = []

    lb = tk.Listbox(root, selectmode=tk.SINGLE)
    lb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 5))

    def refresh(sel=None):
        lb.delete(0, tk.END)
        for p in files:
            lb.insert(tk.END, os.path.basename(p) + "   —   " + p)
        if sel is not None and 0 <= sel < len(files):
            lb.selection_set(sel)

    def add():
        paths = filedialog.askopenfilenames(
            title="Выберите файлы",
            filetypes=[("Поддерживаемые", " ".join("*" + e for e in sorted(SUPPORTED))),
                       ("Все файлы", "*.*")])
        files.extend(paths)
        refresh()

    def selected():
        s = lb.curselection()
        return s[0] if s else None

    def remove():
        i = selected()
        if i is not None:
            files.pop(i)
            refresh(min(i, len(files) - 1))

    def move(delta):
        i = selected()
        if i is None:
            return
        j = i + delta
        if 0 <= j < len(files):
            files[i], files[j] = files[j], files[i]
            refresh(j)

    btns = tk.Frame(root)
    btns.pack(fill=tk.X, padx=10)
    for text, cmd in [("Добавить", add), ("Удалить", remove),
                      ("▲ Вверх", lambda: move(-1)), ("▼ Вниз", lambda: move(1)),
                      ("Очистить", lambda: (files.clear(), refresh()))]:
        tk.Button(btns, text=text, command=cmd).pack(side=tk.LEFT, padx=2)

    name_frame = tk.Frame(root)
    name_frame.pack(fill=tk.X, padx=10, pady=10)
    tk.Label(name_frame, text="Имя файла:").pack(side=tk.LEFT)
    name_var = tk.StringVar(value="объединённый")
    tk.Entry(name_frame, textvariable=name_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
    tk.Label(name_frame, text=".pdf").pack(side=tk.LEFT)

    def do_merge():
        if not files:
            messagebox.showwarning("Нет файлов", "Добавьте хотя бы один файл.")
            return
        name = name_var.get().strip() or "объединённый"
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        folder = filedialog.askdirectory(title="Куда сохранить?")
        if not folder:
            return
        out = os.path.join(folder, name)
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        if os.path.exists(out) and not messagebox.askyesno(
                "Файл существует", f"{out}\nуже существует. Перезаписать?"):
            return
        try:
            result = merge(files, out)
            messagebox.showinfo("Готово", f"Файл сохранён:\n{result}")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    tk.Button(root, text="Объединить в PDF", bg="#4caf50", fg="white",
              font=("Arial", 12, "bold"), command=do_merge).pack(pady=(0, 10))

    root.mainloop()


def main():
    if len(sys.argv) == 1:
        run_gui()
        return
    if sys.argv[1] == "split":
        ap = argparse.ArgumentParser(prog="merge_files.py split",
                                     description="Разделить файл на страницы/части")
        ap.add_argument("file")
        g = ap.add_mutually_exclusive_group()
        g.add_argument("--every", type=int, help="по N страниц в файле")
        g.add_argument("--ranges", help='каждый диапазон отдельно, напр. "1-3,5,8-"')
        g.add_argument("--extract", help='выбранные страницы в один файл, напр. "1,3,5-7"')
        ap.add_argument("-o", "--out-dir", default=".", help="папка для результатов")
        ap.add_argument("-p", "--prefix", help="начало имени файлов")
        ap.add_argument("-f", "--format", default="pdf", choices=["pdf", "jpg", "png"])
        a = ap.parse_args(sys.argv[2:])
        total = len(load_reader(a.file).pages)
        if a.every:
            groups = make_groups(total, "every", a.every)
        elif a.ranges:
            groups = make_groups(total, "ranges", a.ranges)
        elif a.extract:
            groups = make_groups(total, "extract", a.extract)
        else:
            groups = make_groups(total, "each")
        for out in split(a.file, a.out_dir, groups, a.prefix, a.format):
            print(out)
        return
    ap = argparse.ArgumentParser(description="Объединить PDF/изображения/txt в один PDF")
    ap.add_argument("files", nargs="+", help="входные файлы (в нужном порядке)")
    ap.add_argument("-o", "--output", help="имя итогового файла")
    args = ap.parse_args()
    out = args.output or input("Введите имя итогового файла: ").strip() or "объединённый"
    print("Готово:", merge(args.files, out))


if __name__ == "__main__":
    main()
