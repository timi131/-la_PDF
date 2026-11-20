"""
Генератор печатей и штампов (PNG с прозрачным фоном) и наложение их на PDF.

    from stamp import round_stamp, rect_stamp, apply_stamp
    img = round_stamp(outer_text='ООО "РОМАШКА"', center_lines=["Для", "документов"])
    img.save("печать.png")
"""
import io
import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
def _circle(draw, cx, cy, r, width, color, dashed=False):
    """Окружность; dashed=True — пунктирная (прерывистая) линия."""
    if width <= 0 or r <= 0:
        return
    box = (cx - r, cy - r, cx + r, cy + r)
    if not dashed:
        draw.ellipse(box, outline=color, width=int(width))
        return
    # длина штриха ~ 3 толщины линии, промежуток ~ 1.6 толщины
    circ = 2 * math.pi * r
    n = max(8, int(circ / (width * 4.6)))
    step = 360 / n
    for i in range(n):
        a0 = i * step
        draw.arc(box, a0, a0 + step * 0.65, fill=color, width=int(width))


def _dashed_rect(draw, box, width, color):
    """Прямоугольник пунктиром."""
    x0, y0, x1, y1 = box
    dash, gap = width * 3, width * 1.6
    w = int(width)
    for (ax, ay, bx, by) in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
        length = math.hypot(bx - ax, by - ay)
        n = max(1, int((length + gap) / (dash + gap)))
        real_gap = (length - n * dash) / max(n - 1, 1) if n > 1 else 0
        ux, uy = (bx - ax) / length, (by - ay) / length
        t = 0.0
        for _ in range(n):
            e = min(t + dash, length)
            draw.line((ax + ux * t, ay + uy * t, ax + ux * e, ay + uy * e), fill=color, width=w)
            t = e + real_gap


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


def _noise(rng, w, h, cell):
    """Плавный случайный шум 0..1 с размером «пятна» около cell пикселей."""
    import numpy as np
    sw, sh = max(2, int(w / cell) + 2), max(2, int(h / cell) + 2)
    small = Image.fromarray((rng.random((sh, sw)) * 255).astype("uint8"), "L")
    return np.asarray(small.resize((w, h), Image.BICUBIC), dtype="float32") / 255


def _wear(img, wear=0.0, breaks=0.0, uneven=0.0, seed=1):
    """Эффекты настоящего оттиска (все параметры 0..1):
      wear   — потёртость: мелкие пятнышки и зерно без краски
      breaks — прерывистость: разрывы линий и букв, царапины
      uneven — неравномерный нажим: одна сторона бледнее, краска пятнами
    """
    if wear <= 0 and breaks <= 0 and uneven <= 0:
        return img
    import numpy as np
    rng = np.random.default_rng(seed)
    w, h = img.size
    base = max(w, h)
    k = np.ones((h, w), dtype="float32")

    if uneven > 0:
        # градиент нажима в случайном направлении + крупные пятна
        ang = rng.random() * 2 * math.pi
        yy, xx = np.mgrid[0:h, 0:w].astype("float32")
        g = ((xx - w / 2) * math.cos(ang) + (yy - h / 2) * math.sin(ang)) / (base / 2)
        g = (g + 1) / 2                                   # 0..1 поперёк печати
        k *= 1 - uneven * 0.65 * np.clip(g, 0, 1) ** 1.5
        k *= 1 - uneven * 0.45 * _noise(rng, w, h, base / 6)

    if wear > 0:
        # средние пятна + мелкое зерно; чем больше wear, тем больше «дыр»
        n1 = _noise(rng, w, h, base / 70)
        n2 = _noise(rng, w, h, base / 220)
        mix = n1 * 0.6 + n2 * 0.4
        thr = 0.30 + 0.32 * wear
        holes = np.clip((mix - (1 - thr)) / 0.06, 0, 1)   # 1 там, где краски нет
        grain = rng.random((h, w)).astype("float32")
        k *= 1 - holes
        k *= 1 - (grain < wear * 0.22) * 0.85

    if breaks > 0:
        # крупные разрывы: вытянутые «пустые» участки
        n3 = _noise(rng, w, h, base / 14)
        thr = 0.04 + 0.30 * breaks
        k *= np.clip((n3 - thr) / 0.05, 0, 1)
        # разрывы по секторам (как будто печать не пропечаталась местами)
        yy, xx = np.mgrid[0:h, 0:w].astype("float32")
        theta = (np.degrees(np.arctan2(yy - h / 2, xx - w / 2)) + 360) % 360
        for _ in range(int(2 + breaks * 8)):
            c0 = rng.random() * 360
            width = 2 + rng.random() * 10 * breaks
            d = np.abs((theta - c0 + 180) % 360 - 180)
            k *= np.clip((d - width * 0.6) / (width * 0.4 + 1.5), 0, 1)
        # царапины
        mask = Image.new("L", (w, h), 255)
        md = ImageDraw.Draw(mask)
        for _ in range(int(breaks * 7)):
            x0, y0 = rng.random() * w, rng.random() * h
            a = rng.random() * math.pi
            ln = base * (0.15 + rng.random() * 0.45)
            md.line((x0, y0, x0 + ln * math.cos(a), y0 + ln * math.sin(a)), fill=0,
                    width=max(2, int(base * (0.003 + rng.random() * 0.006))))
        mask = mask.filter(ImageFilter.GaussianBlur(base / 900))
        k *= np.asarray(mask, dtype="float32") / 255

    alpha = np.asarray(img.getchannel("A"), dtype="float32") * np.clip(k, 0, 1)
    img.putalpha(Image.fromarray(alpha.astype("uint8"), "L"))
    return img


def _finish(img, opacity, rotation, seed, wear=0.0, breaks=0.0, uneven=0.0):
    img = _wear(img, wear, breaks, uneven, seed)
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
                fill_circle=True, separator=" ★ ", serif=False, bold=True, dashed=False,
                wear=0.0, breaks=0.0, uneven=0.0, opacity=1.0, rotation=0, seed=1, ink=None):
    """Круглая печать.
      outer_text  — текст по внешнему кругу
      bottom_text — текст по низу внешнего круга (тогда outer_text идёт дугой сверху)
      inner_text  — текст по внутреннему кольцу (например, ИНН / ОГРН)
      center_lines — строки в центре
      emblem      — "none" | "star" (звезда над центральным текстом)
      border      — толщина рамки, % от размера
      dashed      — пунктирная (прерывистая) рамка
      wear        — 0..1, потёртость (пятнышки без краски)
      breaks      — 0..1, прерывистость (разрывы линий и букв, царапины)
      uneven      — 0..1, неравномерный нажим
      opacity     — 0..1, непрозрачность (1 — без прозрачности)
      rotation    — поворот печати, градусы
      seed        — номер варианта случайного узора
      ink         — устаревший параметр: задаёт wear и uneven одним числом
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

    _circle(d, c, c, R, bw, col, dashed)
    r = R - bw / 2
    if double_border:
        r -= S * 0.012
        _circle(d, c, c, r - thin / 2, thin, col, dashed)
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
        _circle(d, c, c, r - thin / 2, thin, col, dashed)
        r -= thin

    # внутреннее кольцо
    if inner_text:
        fs = S * 0.052
        ring2 = fs * 1.5
        r_text = r - ring2 / 2
        main = inner_text.strip()
        _arc_text(img, main, c, r_text, fs, rgb, fill_circle, separator, bold, serif)
        r -= ring2
        _circle(d, c, c, r - thin / 2, thin, col, dashed)
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

    if ink is not None:
        wear, uneven = wear or ink, uneven or ink
    return _finish(img, opacity, rotation, seed, wear, breaks, uneven)


def rect_stamp(lines=("ОБРАЗЕЦ",), color="#1f3fbf", width=700, height=300, border=2.0,
               double_border=True, corner=6, serif=False, bold=True, dashed=False,
               wear=0.0, breaks=0.0, uneven=0.0, opacity=1.0, rotation=0, seed=1, ink=None):
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
    if dashed:
        _dashed_rect(d, (m, m, W - m, H - m), bw, col)
    else:
        d.rounded_rectangle((m, m, W - m, H - m), radius=rad, outline=col, width=int(bw))
    inset = m
    if double_border:
        inset = m + bw / 2 + min(W, H) * 0.03
        if dashed:
            _dashed_rect(d, (inset, inset, W - inset, H - inset), thin, col)
        else:
            d.rounded_rectangle((inset, inset, W - inset, H - inset), radius=max(0, rad * 0.7),
                                outline=col, width=int(thin))
    lines = [ln for ln in lines if ln.strip()]
    if lines:
        avail_h = H - 2 * inset - min(W, H) * 0.08
        size_c = min(avail_h / len(lines) / 1.2, H * 0.4)
        _center_lines(img, lines, W / 2, H / 2, W - 2 * inset - W * 0.06, size_c, rgb, bold, serif)
    if ink is not None:
        wear, uneven = wear or ink, uneven or ink
    return _finish(img, opacity, rotation, seed, wear, breaks, uneven)


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


def apply_images(pdf_path, items, output, pages=None):
    """Ставит на страницы PDF несколько картинок (печать, подпись…).
      items — список словарей {"png": байты, "x": 0..1, "y": 0..1, "width_mm": мм}
              (x, y — центр картинки в долях страницы; накладываются по порядку)
      pages — номера страниц с 0 (None — все)."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    from pdf_edit import open_doc
    doc = open_doc(pdf_path)
    sizes = []
    for it in items:
        im = Image.open(io.BytesIO(it["png"]))
        w_pt = it["width_mm"] * 72 / 25.4
        sizes.append((w_pt, w_pt * im.height / im.width))
    for i, page in enumerate(doc):
        if pages is not None and i not in pages:
            continue
        if page.rotation:
            page.remove_rotation()
        r = page.rect
        m = min(r.width, r.height) * 0.03
        for it, (w_pt, h_pt) in zip(items, sizes):
            # не даём картинке выйти за край страницы
            k = min(1.0, (r.width - 2 * m) / w_pt, (r.height - 2 * m) / h_pt)
            w, h = w_pt * k, h_pt * k
            cx = min(max(r.width * it["x"], m + w / 2), r.width - m - w / 2)
            cy = min(max(r.height * it["y"], m + h / 2), r.height - m - h / 2)
            page.insert_image(fitz.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
                              stream=it["png"], overlay=True)
    if not output.lower().endswith(".pdf"):
        output += ".pdf"
    doc.save(output, garbage=3, deflate=True)
    doc.close()
    return output


def apply_stamp(pdf_path, stamp_png, output, pages=None, x=0.75, y=0.85, width_mm=40):
    """Ставит одну печать (PNG-байты) на страницы PDF."""
    return apply_images(pdf_path, [{"png": stamp_png, "x": x, "y": y, "width_mm": width_mm}],
                        output, pages)
