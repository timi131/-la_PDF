"""
Генератор печатей и штампов (PNG с прозрачным фоном) и наложение их на PDF.

    from stamp import round_stamp, rect_stamp, apply_stamp
    img = round_stamp(outer_text='ООО "РОМАШКА"', center_lines=["Для", "документов"])
    img.save("печать.png")
"""
import io
import math
import os
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

SS = 3  # суперсэмплинг для гладких краёв

_FONTS = {
    True: [r"C:\Windows\Fonts\arialbd.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
           "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"],
    False: [r"C:\Windows\Fonts\arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf"],
}
_SERIF = {
    True: [r"C:\Windows\Fonts\timesbd.ttf", "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"],
    False: [r"C:\Windows\Fonts\times.ttf", "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"],
}


def get_font(size, bold=True, serif=False):
    for path in (_SERIF if serif else _FONTS)[bold] + _FONTS[bold] + _FONTS[not bold]:
        if os.path.exists(path):
            return ImageFont.truetype(path, max(1, int(size)))
    try:
        return ImageFont.load_default(size=max(1, int(size)))
    except TypeError:
        return ImageFont.load_default()


def hex_to_rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


# ---------------------------------------------------------------- рисование
def _circle(draw, cx, cy, r, width, color):
    if width > 0 and r > 0:
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=int(width))


def _text_on_circle(img, text, cx, cy, radius, font, color, span_deg=None, bottom=False,
                    center_chars=None, center_deg=None):
    """Текст по окружности. Углы — от верхней точки по часовой стрелке.
      span_deg=None — естественная ширина; 360 — растянуть по всему кругу.
      bottom=True — текст снизу, читается слева направо.
      center_chars — сколько первых символов центрировать (остальное — разделитель)."""
    if not text:
        return
    ascent, descent = font.getmetrics()
    h = ascent + descent
    char_deg = [math.degrees(font.getlength(ch) / radius) for ch in text]
    natural = sum(char_deg)
    n = len(text)
    gap = 0.0
    if span_deg:
        gap = (span_deg - natural) / (n if span_deg >= 360 else max(n - 1, 1))
        gap = max(gap, 0) if span_deg < 360 else gap
    # центры символов при старте с 0°
    mids, pos = [], 0.0
    for d in char_deg:
        mids.append(pos + d / 2)
        pos += d + gap
    k = (center_chars or n) - 1
    middle = (mids[0] - char_deg[0] / 2 + mids[k] + char_deg[k] / 2) / 2
    target = center_deg if center_deg is not None else (180 if bottom else 0)
    for ch, m in zip(text, mids):
        mid = target - (m - middle) if bottom else target + (m - middle)
        if not ch.strip():
            continue
        w = int(font.getlength(ch)) + 4
        tile = Image.new("L", (w, h + 4), 0)
        ImageDraw.Draw(tile).text((2, 2), ch, font=font, fill=255)
        tile = tile.rotate((180 - mid) if bottom else -mid, resample=Image.BICUBIC, expand=True)
        a = math.radians(mid)
        x = cx + radius * math.sin(a)
        y = cy - radius * math.cos(a)
        colored = Image.new("RGBA", tile.size, color + (255,))
        colored.putalpha(tile)
        img.alpha_composite(colored, (int(x - tile.width / 2), int(y - tile.height / 2)))


def _arc_text(img, main, c, r_text, size, rgb, fill_circle, separator, bold, serif):
    """Кольцевая надпись: длинная — по всему кругу с разделителем, короткая — дугой сверху."""
    f = get_font(size, bold, serif)
    natural = math.degrees(sum(f.getlength(ch) for ch in main) / r_text)
    if not fill_circle or natural < 200:
        font = _fit_arc_font(main, r_text, 300, size, bold, serif)
        nat = math.degrees(sum(font.getlength(ch) for ch in main) / r_text)
        _text_on_circle(img, main, c, c, r_text, font, rgb, span_deg=min(nat * 1.25, 300))
    else:
        full = main + separator
        font = _fit_arc_font(full, r_text, 349, size, bold, serif)
        _text_on_circle(img, full, c, c, r_text, font, rgb, span_deg=360, center_chars=len(main))


def _fit_arc_font(text, radius, max_deg, size, bold, serif):
    """Подбирает размер шрифта, чтобы текст занял не больше max_deg градусов дуги."""
    while size > 6:
        f = get_font(size, bold, serif)
        if math.degrees(sum(f.getlength(ch) for ch in text) / radius) <= max_deg:
            return f
        size -= 1
    return get_font(size, bold, serif)


def _fit_font(text, max_width, size, bold=True, serif=False, min_size=6):
    f = get_font(size, bold, serif)
    while size > min_size and f.getlength(text) > max_width:
        size -= 1
        f = get_font(size, bold, serif)
    return f


def _center_lines(img, lines, cx, cy, max_w, size, color, bold=True, serif=False, line_gap=1.15):
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        return
    draw = ImageDraw.Draw(img)
    fonts = [_fit_font(ln, max_w, size, bold, serif) for ln in lines]
    heights = [f.size * line_gap for f in fonts]
    y = cy - sum(heights) / 2
    for ln, f, hh in zip(lines, fonts, heights):
        draw.text((cx, y + hh / 2), ln, font=f, fill=color + (255,), anchor="mm")
        y += hh


def _star(draw, cx, cy, r, color):
    pts = []
    for i in range(10):
        rr = r if i % 2 == 0 else r * 0.4
        a = math.radians(-90 + i * 36)
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    draw.polygon(pts, fill=color + (255,))


def _ink(img, strength, seed=None):
    """Эффект настоящего оттиска: неравномерная краска, потёртости."""
    if strength <= 0:
        return img
    rnd = random.Random(seed)
    w, h = img.size
    # крупные пятна неравномерного прижатия
    small = Image.new("L", (max(1, w // 40), max(1, h // 40)))
    small.putdata([int(255 - rnd.random() * 140 * strength) for _ in range(small.width * small.height)])
    blot = small.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(w / 60))
    # мелкие «проплешины»
    noise = Image.effect_noise((w, h), 80).point(lambda v: 0 if v < 255 * (0.18 * strength) + 40 else 255)
    noise = noise.filter(ImageFilter.GaussianBlur(1.2)).point(lambda v: 255 if v > 90 else int(v * 2))
    alpha = img.getchannel("A")
    alpha = ImageChops.multiply(alpha, blot)
    alpha = ImageChops.multiply(alpha, noise)
    img.putalpha(alpha)
    return img


def _finish(img, size, opacity, ink, rotation, seed):
    img = _ink(img, ink, seed)
    if rotation:
        img = img.rotate(-rotation, resample=Image.BICUBIC, expand=True)
    img = img.resize((int(img.width / SS), int(img.height / SS)), Image.LANCZOS)
    if opacity < 1:
        img.putalpha(img.getchannel("A").point(lambda v: int(v * opacity)))
    return img


# ---------------------------------------------------------------- печати
def round_stamp(outer_text="ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ", bottom_text="",
                inner_text="", center_lines=("ОБРАЗЕЦ",), emblem="none",
                color="#1f3fbf", size=600, border=2.2, double_border=True,
                fill_circle=True, separator=" ★ ", serif=False, bold=True,
                ink=0.0, opacity=1.0, rotation=0, seed=1):
    """Круглая печать.
      outer_text  — текст по внешнему кругу
      bottom_text — текст по низу внешнего круга (тогда outer_text идёт дугой сверху)
      inner_text  — текст по внутреннему кольцу (например, ИНН / ОГРН)
      center_lines — строки в центре
      emblem      — "none" | "star" (звезда над центральным текстом)
      border      — толщина рамки, % от размера
      ink         — 0..1, эффект настоящего оттиска
      rotation    — поворот печати, градусы
    """
    rgb = hex_to_rgb(color)
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = S / 2
    bw = max(2, S * border / 100)
    thin = max(2, bw * 0.4)
    R = S / 2 - bw / 2 - 2
    col = rgb + (255,)

    _circle(d, c, c, R, bw, col)
    r = R - bw / 2
    if double_border:
        r -= S * 0.012
        _circle(d, c, c, r - thin / 2, thin, col)
        r -= thin

    # внешний текст
    outer_fs = S * 0.075
    ring_h = outer_fs * 1.45
    if outer_text:
        r_text = r - ring_h / 2
        main = outer_text.strip()
        if bottom_text:
            fb = _fit_arc_font(bottom_text.strip(), r_text, 100, outer_fs * 0.9, bold, serif)
            bottom_deg = math.degrees(sum(fb.getlength(ch) for ch in bottom_text.strip()) / r_text)
            top_max = 360 - bottom_deg - 40
            font = _fit_arc_font(main, r_text, top_max, outer_fs, bold, serif)
            _text_on_circle(img, main, c, c, r_text, font, rgb)
            _text_on_circle(img, bottom_text.strip(), c, c, r_text, fb, rgb, bottom=True)
        else:
            _arc_text(img, main, c, r_text, outer_fs, rgb, fill_circle, separator, bold, serif)
        r -= ring_h
        _circle(d, c, c, r - thin / 2, thin, col)
        r -= thin

    # внутреннее кольцо
    if inner_text:
        fs = S * 0.052
        ring2 = fs * 1.5
        r_text = r - ring2 / 2
        main = inner_text.strip()
        _arc_text(img, main, c, r_text, fs, rgb, fill_circle, separator, bold, serif)
        r -= ring2
        _circle(d, c, c, r - thin / 2, thin, col)
        r -= thin

    # центр
    lines = [ln for ln in (center_lines or []) if ln.strip()]
    cy = c
    if emblem == "star":
        star_r = r * (0.28 if lines else 0.55)
        sy = c - (r * 0.38 if lines else 0)
        _star(d, c, sy, star_r, rgb)
        cy = c + r * 0.22
    max_w = r * 1.55
    if lines:
        size_c = S * (0.085 if len(lines) <= 2 else 0.065)
        _center_lines(img, lines, c, cy, max_w, size_c, rgb, bold, serif)

    return _finish(img, size, opacity, ink, rotation, seed)


def rect_stamp(lines=("ОБРАЗЕЦ",), color="#1f3fbf", width=700, height=300, border=2.0,
               double_border=True, corner=6, serif=False, bold=True,
               ink=0.0, opacity=1.0, rotation=0, seed=1):
    """Прямоугольный штамп (например, «ОПЛАЧЕНО», «КОПИЯ ВЕРНА», угловой штамп)."""
    rgb = hex_to_rgb(color)
    W, H = width * SS, height * SS
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = rgb + (255,)
    bw = max(2, min(W, H) * border / 100 * 2)
    thin = max(2, bw * 0.45)
    rad = min(W, H) * corner / 100
    m = bw / 2 + 2
    d.rounded_rectangle((m, m, W - m, H - m), radius=rad, outline=col, width=int(bw))
    inset = m
    if double_border:
        inset = m + bw / 2 + min(W, H) * 0.03
        d.rounded_rectangle((inset, inset, W - inset, H - inset), radius=max(0, rad * 0.7),
                            outline=col, width=int(thin))
    lines = [ln for ln in lines if ln.strip()]
    if lines:
        avail_h = H - 2 * inset - min(W, H) * 0.08
        size_c = min(avail_h / len(lines) / 1.2, H * 0.4)
        _center_lines(img, lines, W / 2, H / 2, W - 2 * inset - W * 0.06, size_c, rgb, bold, serif)
    return _finish(img, None, opacity, ink, rotation, seed)


def to_png_bytes(img, dpi=300):
    buf = io.BytesIO()
    img.save(buf, "PNG", dpi=(dpi, dpi))
    return buf.getvalue()


# ---------------------------------------------------------------- наложение на PDF
POSITIONS = {
    "Снизу справа": (0.75, 0.85), "Снизу по центру": (0.5, 0.85), "Снизу слева": (0.25, 0.85),
    "По центру": (0.5, 0.5),
    "Сверху справа": (0.75, 0.15), "Сверху слева": (0.25, 0.15),
}


def apply_stamp(pdf_path, stamp_png, output, pages=None, x=0.75, y=0.85, width_mm=40):
    """Ставит печать (PNG-байты) на страницы PDF.
      pages — номера страниц с 0 (None — все); x, y — центр печати в долях страницы."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    from pdf_edit import open_doc
    doc = open_doc(pdf_path)
    im = Image.open(io.BytesIO(stamp_png))
    w_pt = width_mm * 72 / 25.4
    h_pt = w_pt * im.height / im.width
    for i, page in enumerate(doc):
        if pages is not None and i not in pages:
            continue
        if page.rotation:
            page.remove_rotation()
        r = page.rect
        # не даём печати выйти за край страницы
        m = min(r.width, r.height) * 0.03
        k = min(1.0, (r.width - 2 * m) / w_pt, (r.height - 2 * m) / h_pt)
        w, h = w_pt * k, h_pt * k
        cx = min(max(r.width * x, m + w / 2), r.width - m - w / 2)
        cy = min(max(r.height * y, m + h / 2), r.height - m - h / 2)
        rect = fitz.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        page.insert_image(rect, stream=stamp_png, overlay=True)
    if not output.lower().endswith(".pdf"):
        output += ".pdf"
    doc.save(output, garbage=3, deflate=True)
    doc.close()
    return output
