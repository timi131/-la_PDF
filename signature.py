"""
Генератор подписи (PNG с прозрачным фоном).

  * signature_from_text  — подпись из имени рукописным шрифтом
                           (наклон, «живая» рука, росчерк)
  * signature_from_photo — своя подпись с фотографии/скана: убирает фон,
                           делает его прозрачным, перекрашивает чернила

Шрифты лежат в папке fonts/ (лицензия SIL Open Font License).
"""
import io
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONTS = {
    "Изящный (Marck Script)": "MarckScript-Regular.ttf",
    "Каллиграфический (Great Vibes)": "GreatVibes-Regular.ttf",
    "Быстрый (Bad Script)": "BadScript-Regular.ttf",
    "Простой (Caveat)": "Caveat.ttf",
}
FLOURISHES = {
    "Нет": "none",
    "Подчёркивание": "underline",
    "Петля": "loop",
    "Хвост вверх": "tail",
}
SS = 3  # суперсэмплинг


def hex_to_rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _font(name, size):
    file = FONTS.get(name, name)
    path = file if os.path.isabs(file) else os.path.join(FONT_DIR, file)
    return ImageFont.truetype(path, int(size))


def _densify(pts, step):
    """Добавляет точки, чтобы расстояние между соседними было не больше step."""
    out = [pts[0]]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
        for i in range(1, n + 1):
            out.append((x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n))
    return out


def _stroke(draw, pts, width, color, taper=True):
    """Линия переменной толщины по точкам (имитация нажима пера)."""
    pts = _densify(pts, max(0.5, width * 0.08))
    n = len(pts)
    for i, (x, y) in enumerate(pts):
        t = i / max(n - 1, 1)
        k = math.sin(math.pi * t) ** 0.6 if taper else 1.0   # тоньше в начале и конце
        r = max(0.6, width / 2 * (0.35 + 0.65 * k))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)


def _bezier(p0, p1, p2, p3, steps=200):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        pts.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return pts


def signature_from_text(text, font="Изящный (Marck Script)", size=110, slant=12, color="#1b2a8f",
                        thickness=0.0, jitter=0.35, mode="full", keep_letters=3,
                        flourish="underline", pressure=0.3, seed=1):
    """Подпись из текста.
      text        — например «Иванов» или «И. Иванов»
      mode        — "full": весь текст; "short": первые keep_letters букв + волнистый росчерк
      slant       — наклон вправо, градусы
      thickness   — 0..1, дополнительная толщина линий
      jitter      — 0..1, «живость» руки (разные наклон, размер и высота букв)
      flourish    — "none" | "underline" | "loop" | "tail"
      pressure    — 0..1, неравномерность нажима (чернила светлее/темнее)
      seed        — номер варианта (одинаковый номер — одинаковая подпись)
    """
    rng = np.random.default_rng(seed)
    rgb = hex_to_rgb(color)
    ink = rgb + (255,)
    text = (text or "").strip() or "Подпись"
    if mode == "short":
        letters = [c for c in text if c.strip()]
        text = "".join(letters[:max(1, keep_letters)])

    S = size * SS
    f = _font(font, S)
    stroke_w = max(1, int(S * 0.012 * thickness))
    pen = max(2.0, S * 0.042 * (1 + thickness * 0.8))       # толщина «пера» для росчерков
    ascent, descent = f.getmetrics()

    W = int(f.getlength(text) * 1.25 + S * (3.0 if mode == "short" else 1.6))
    H = int(S * 3.2)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    base_y = H * 0.55
    x = S * 0.4
    first_x = x
    tops, bottoms = [], []
    # --- буквы с небольшими случайными отклонениями
    start_x = x
    drift = 0.0
    for i, ch in enumerate(text):
        # позиция буквы — как в обычной строке (с учётом кернинга) + небольшой «разброс»
        x = start_x + f.getlength(text[:i]) + drift
        adv = f.getlength(text[:i + 1]) - f.getlength(text[:i])
        if not ch.strip():
            drift += adv * (rng.random() - 0.6) * 0.5
            continue
        sc = 1 + (rng.random() - 0.5) * 0.22 * jitter
        fs = _font(font, S * sc)
        tw = int(fs.getlength(ch) + S * 0.6)
        th = int(S * 2.2)
        tile = Image.new("L", (tw, th), 0)
        ImageDraw.Draw(tile).text((S * 0.3, th * 0.6), ch, font=fs, fill=255, anchor="ls",
                                  stroke_width=stroke_w, stroke_fill=255)
        tile = tile.rotate((rng.random() - 0.5) * 12 * jitter, resample=Image.BICUBIC,
                           center=(S * 0.3, th * 0.6))
        dy = (rng.random() - 0.5) * S * 0.12 * jitter
        layer = Image.new("RGBA", tile.size, ink)
        layer.putalpha(tile)
        px, py = int(x - S * 0.3), int(base_y + dy - th * 0.6)
        img.alpha_composite(layer, (max(0, px), max(0, py)))
        bb = tile.getbbox()
        if bb:
            tops.append(py + bb[1])
            bottoms.append(py + bb[3])
        drift += adv * (sc - 1) + adv * (rng.random() - 0.6) * 0.06 * jitter
        x += adv
    end_x = start_x + f.getlength(text) + drift
    top = min(tops) if tops else base_y - ascent
    d = ImageDraw.Draw(img)

    # --- волнистый росчерк после первых букв
    if mode == "short":
        amp = S * (0.22 + 0.12 * rng.random())
        length = S * (1.6 + 0.8 * rng.random())
        waves = 2.5 + rng.random() * 2
        pts = []
        for i in range(260):
            t = i / 259
            yy = base_y - S * 0.15 - amp * (1 - 0.6 * t) * math.sin(t * waves * 2 * math.pi + 0.4) \
                - S * 0.25 * t
            pts.append((end_x - S * 0.05 + length * t, yy))
        _stroke(d, pts, pen, ink)
        end_x += length

    # --- росчерк
    if flourish == "underline":
        y0 = base_y + S * 0.18
        pts = _bezier((first_x - S * 0.1, y0 + S * 0.05),
                      (first_x + (end_x - first_x) * 0.3, y0 + S * 0.35),
                      (first_x + (end_x - first_x) * 0.75, y0 + S * 0.1 * rng.random()),
                      (end_x + S * 0.5, y0 - S * 0.35))
        _stroke(d, pts, pen, ink)
    elif flourish == "loop":
        sx, sy = end_x - S * 0.1, base_y - S * 0.1
        pts = _bezier((sx, sy), (sx + S * 0.9, sy + S * 0.8), (first_x - S * 0.6, base_y + S * 0.9),
                      (first_x + S * 0.2, base_y + S * 0.15))
        pts += _bezier(pts[-1], (first_x + S * 1.0, base_y - S * 0.25),
                       (end_x + S * 0.2, base_y + S * 0.2), (end_x + S * 0.9, base_y - S * 0.1))
        _stroke(d, pts, pen, ink)
    elif flourish == "tail":
        sx, sy = end_x - S * 0.05, base_y
        pts = _bezier((sx, sy), (sx + S * 0.4, sy + S * 0.3), (sx + S * 0.7, sy - S * 0.2),
                      (sx + S * 1.1, top - S * 0.1))
        _stroke(d, pts, pen, ink)

    # --- наклон (сдвиг по горизонтали, как у письма от руки)
    if slant:
        k = math.tan(math.radians(slant))
        extra = int(abs(k) * H)
        img = img.transform((img.width + extra, img.height), Image.AFFINE,
                            (1, k, -extra if k > 0 else 0, 0, 1, 0), resample=Image.BICUBIC)

    # --- неравномерный нажим
    if pressure > 0:
        a = np.asarray(img.getchannel("A"), dtype="float32")
        h, w = a.shape
        small = Image.fromarray((rng.random((max(2, h // 60), max(2, w // 60))) * 255).astype("uint8"))
        n = np.asarray(small.resize((w, h), Image.BICUBIC), dtype="float32") / 255
        a *= 1 - pressure * 0.55 * n
        img.putalpha(Image.fromarray(a.astype("uint8")))

    return _crop_down(img)


def _crop_down(img, pad=0.06):
    bb = img.getchannel("A").getbbox()
    if bb:
        p = int(max(bb[2] - bb[0], bb[3] - bb[1]) * pad)
        img = img.crop((max(0, bb[0] - p), max(0, bb[1] - p),
                        min(img.width, bb[2] + p), min(img.height, bb[3] + p)))
    return img.resize((max(1, img.width // SS), max(1, img.height // SS)), Image.LANCZOS)


def signature_from_photo(data, color="#1b2a8f", keep_color=False, sensitivity=0.5,
                         smooth=True, max_side=1600):
    """Подпись с фото или скана: убирает бумагу, делает фон прозрачным.
      data        — байты изображения (JPG/PNG/…)
      keep_color  — оставить исходный цвет чернил вместо color
      sensitivity — 0..1, чем больше, тем больше светлых штрихов считается чернилами
    """
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    gray = ImageOps.grayscale(im)
    # выравниваем освещение: вычитаем размытый фон
    bg = gray.filter(ImageFilter.GaussianBlur(max(gray.size) / 25))
    g = np.asarray(gray, dtype="float32")
    b = np.asarray(bg, dtype="float32")
    darkness = np.clip((b - g) / np.maximum(b, 1), 0, 1)         # 0 — бумага, 1 — чернила
    # порог Оцу по тёмности
    hist, edges = np.histogram(darkness, bins=256, range=(0, 1))
    p = hist / max(hist.sum(), 1)
    omega = np.cumsum(p)
    mu = np.cumsum(p * edges[:-1])
    sigma = (mu[-1] * omega - mu) ** 2 / np.maximum(omega * (1 - omega), 1e-9)
    thr = edges[int(np.argmax(sigma))]
    thr = float(np.clip(thr * (1.6 - sensitivity * 1.2), 0.03, 0.6))
    alpha = np.clip((darkness - thr * 0.6) / max(thr * 0.8, 1e-3), 0, 1)
    a = Image.fromarray((alpha * 255).astype("uint8"))
    if smooth:
        a = a.filter(ImageFilter.MedianFilter(3))
    if keep_color:
        rgb = im
    else:
        rgb = Image.new("RGB", im.size, hex_to_rgb(color))
    out = rgb.convert("RGBA")
    out.putalpha(a)
    bb = a.point(lambda v: 255 if v > 40 else 0).getbbox()
    if not bb:
        raise ValueError("На изображении не найдена подпись. Попробуйте увеличить чувствительность.")
    pad = int(max(bb[2] - bb[0], bb[3] - bb[1]) * 0.05)
    return out.crop((max(0, bb[0] - pad), max(0, bb[1] - pad),
                     min(out.width, bb[2] + pad), min(out.height, bb[3] + pad)))


def to_png_bytes(img, dpi=300):
    buf = io.BytesIO()
    img.save(buf, "PNG", dpi=(dpi, dpi))
    return buf.getvalue()
