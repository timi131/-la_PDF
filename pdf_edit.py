"""
Редактирование PDF: поворот, удаление, перестановка, дублирование страниц,
пустые страницы, водяной знак, нумерация, пароль, сжатие.

Требует:  pip install pymupdf
"""
import os

try:
    import pymupdf as fitz
except ImportError:  # старые версии PyMuPDF
    import fitz

from merge_files import load_reader

A4 = (595, 842)

# Шрифты с кириллицей (для водяного знака и номеров страниц)
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]


def find_font():
    for f in _FONT_CANDIDATES:
        if os.path.exists(f):
            return f
    return None


def open_doc(path):
    """Открывает PDF, изображение или TXT как документ PyMuPDF."""
    if path.lower().endswith(".pdf"):
        return fitz.open(path)
    import io
    from pypdf import PdfWriter
    buf = io.BytesIO()
    w = PdfWriter()
    for p in load_reader(path).pages:
        w.add_page(p)
    w.write(buf)
    return fitz.open(stream=buf.getvalue(), filetype="pdf")


def page_entries(doc_index, doc):
    """Список страниц документа для редактора."""
    return [{"d": doc_index, "p": i, "r": 0} for i in range(len(doc))]


def page_size(docs, e):
    """Видимый размер страницы (с учётом поворота)."""
    if e["d"] is None:
        w, h = e.get("size", A4)
        rot = e["r"]
    else:
        page = docs[e["d"]][e["p"]]
        w, h = page.rect.width, page.rect.height  # уже с учётом исходного поворота
        rot = e["r"]
    return (h, w) if rot % 180 else (w, h)


def render_thumb(docs, e, max_side=130):
    """Миниатюра страницы как PIL.Image."""
    from PIL import Image
    if e["d"] is None:
        w, h = e.get("size", A4)
        k = max_side / max(w, h)
        img = Image.new("RGB", (max(1, int(w * k)), max(1, int(h * k))), "white")
    else:
        page = docs[e["d"]][e["p"]]
        k = max_side / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(k, k), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if e["r"]:
        img = img.rotate(-e["r"], expand=True)
    return img


def _draw_text(page, text, center, fontsize, font, fontfile, color, opacity, angle):
    """Текст по центру точки center, повёрнутый на angle градусов."""
    width = font.text_length(text, fontsize=fontsize)
    start = fitz.Point(center.x - width / 2, center.y + fontsize * 0.35)
    kw = dict(fontsize=fontsize, color=color, overlay=True)
    if fontfile:
        kw.update(fontname="F0", fontfile=fontfile)
    else:
        kw.update(fontname="helv")
    if opacity < 1:
        kw["fill_opacity"] = opacity
    if angle:
        kw["morph"] = (center, fitz.Matrix(1, 1).prerotate(angle))
    page.insert_text(start, text, **kw)


def build_pdf(docs, entries, output, watermark=None, wm_opacity=0.2, wm_size=None,
              numbers=None, number_pos="bottom-center", password=None, compress=True,
              title=None, author=None):
    """
    Собирает итоговый PDF.
      docs      — список открытых документов fitz
      entries   — список страниц {"d": индекс документа | None (пустая), "p": номер, "r": поворот}
      watermark — текст водяного знака (по диагонали)
      numbers   — шаблон номера, напр. "{n}" или "Стр. {n} из {total}"
      password  — пароль на открытие (AES-256)
    """
    if not entries:
        raise ValueError("В документе нет страниц")
    if not output.lower().endswith(".pdf"):
        output += ".pdf"
    out = fitz.open()
    for e in entries:
        if e["d"] is None:
            w, h = e.get("size", A4)
            out.new_page(width=w, height=h)
        else:
            out.insert_pdf(docs[e["d"]], from_page=e["p"], to_page=e["p"])
        page = out[-1]
        if e["r"]:
            page.set_rotation((page.rotation + e["r"]) % 360)

    fontfile = find_font()
    font = fitz.Font(fontfile=fontfile) if fontfile else fitz.Font("helv")
    total = len(out)
    if watermark or numbers:
        for n, page in enumerate(out, 1):
            if page.rotation:
                page.remove_rotation()  # чтобы координаты совпадали с видимой страницей
            r = page.rect
            if watermark:
                size = wm_size or min(r.width, r.height) / max(len(watermark), 4) * 1.6
                _draw_text(page, watermark, fitz.Point(r.width / 2, r.height / 2), size,
                           font, fontfile, (0.5, 0.5, 0.5), wm_opacity, 45)
            if numbers:
                text = numbers.format(n=n, total=total)
                fs = 10
                m = 24
                vert, horiz = number_pos.split("-")
                y = m if vert == "top" else r.height - m
                x = {"left": m + font.text_length(text, fs) / 2,
                     "center": r.width / 2,
                     "right": r.width - m - font.text_length(text, fs) / 2}[horiz]
                _draw_text(page, text, fitz.Point(x, y), fs, font, fontfile, (0.2, 0.2, 0.2), 1, 0)

    meta = out.metadata or {}
    if title:
        meta["title"] = title
    if author:
        meta["author"] = author
    out.set_metadata(meta)

    kw = dict(garbage=4 if compress else 0, deflate=compress, clean=compress)
    if password:
        kw.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
    out.save(output, **kw)
    out.close()
    return output


def rotate_file(path, output, angle, pages=None):
    """Повернуть страницы файла (pages — список номеров с 0, None — все)."""
    doc = open_doc(path)
    entries = page_entries(0, doc)
    for i, e in enumerate(entries):
        if pages is None or i in pages:
            e["r"] = angle % 360
    return build_pdf([doc], entries, output)
