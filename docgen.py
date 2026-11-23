"""
Создание документов по шаблонам Word (.docx).

В шаблоне поля пишутся как {{имя_поля}}, например {{org_name}}, {{inn}}, {{data}}.
Word часто разбивает такое поле на несколько кусочков текста — замена это учитывает
и сохраняет форматирование.

Кроме полей {{…}} распознаются «заглушки» в тексте:
  ХХХХХХ.ru  — доменное имя,
  ХХХХХХ     — номер товарного знака,
  (паспортные или иные … данные …) — паспортные данные физлица.
"""
import datetime as dt
import io
import os
import re
import shutil
import subprocess
import tempfile

import docx

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

# Встроенные шаблоны: файл → название
BUILTIN = {
    "TZ.UL.docx": "Согласие на товарный знак — юрлицо",
    "TZ.IP.docx": "Согласие на товарный знак — ИП",
    "TZ.Fiz.docx": "Согласие на товарный знак — физлицо",
    "Domen.UL.docx": "Согласие на доменное имя — юрлицо",
}

# Известные поля: имя → (подпись в форме, тип, подсказка)
KNOWN = {
    "org_name": ("Наименование организации / ФИО", "text",
                 "Например: ООО «Ромашка», ИП Иванов Иван Иванович или Иванов Иван Иванович"),
    "inn": ("ИНН", "text", ""),
    "director": ("ФИО руководителя", "text",
                 "Проверьте падеж: в шаблоне это поле может стоять после «в лице генерального директора»"),
    "director_short": ("Руководитель (И.О. Фамилия)", "text", ""),
    "srok": ("Срок действия (до)", "date", ""),
    "data": ("Дата документа", "date", ""),
}

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
# Заглушки: ключ → (регулярное выражение, подпись, подсказка)
MARKERS = {
    "__domain__": (re.compile(r"(?<![ХX])[ХX]{3,}\.ru"), "Доменное имя (вместо «ХХХХХХ.ru»)", "Например: romashka.ru"),
    "__tm__": (re.compile(r"(?<![ХX])[ХX]{3,}(?![ХX]|\.ru)"), "Номер товарного знака (вместо «ХХХХХХ»)", "Например: 123456"),
    "__passport__": (re.compile(r"\(паспортные или иные[^)]*\)"), "Паспортные данные (вместо подсказки в скобках)",
                     "Например: паспорт 4510 123456, выдан ГУ МВД России по г. Москве 01.01.2015"),
}

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
DATE_FORMATS = {
    "23.11.2026": lambda d: d.strftime("%d.%m.%Y"),
    "«23» ноября 2026 г.": lambda d: f"«{d.day:02d}» {MONTHS[d.month - 1]} {d.year} г.",
    "23 ноября 2026 г.": lambda d: f"{d.day} {MONTHS[d.month - 1]} {d.year} г.",
}


def format_date(d, fmt):
    return DATE_FORMATS[fmt](d) if isinstance(d, (dt.date, dt.datetime)) else str(d)


# ------------------------------------------------------------------ ФИО
def _gen_word(w, kind, female):
    """Родительный падеж одного слова ФИО (типичные русские окончания)."""
    lw = w.lower()

    def end(e, new):
        return w[:len(w) - len(e)] + (new.upper() if w.isupper() else new)

    if kind == "patronymic":
        if lw.endswith("вич") or lw.endswith("ич"):
            return end("", "а")
        if lw.endswith("вна") or lw.endswith("чна"):
            return end("а", "ы")
        return w
    if kind == "surname":
        if female:
            for e, n in (("ова", "овой"), ("ева", "евой"), ("ёва", "ёвой"), ("ина", "иной"),
                         ("ына", "ыной"), ("ская", "ской"), ("цкая", "цкой"), ("ая", "ой")):
                if lw.endswith(e):
                    return end(e, n)
            return w        # Шевчук, Дюма и т.п. не склоняются
        for e, n in (("ский", "ского"), ("цкий", "цкого"), ("ой", "ого"), ("ый", "ого"), ("ий", "его")):
            if lw.endswith(e):
                return end(e, n)
        if lw[-1:] in "бвгджзклмнпрстфхцчшщ":
            return end("", "а")
        if lw.endswith("й"):
            return end("й", "я")
        if lw.endswith("а") and len(lw) > 2:
            return end("а", "ы")
        return w
    # имя
    exc = {"пётр": "петра", "петр": "петра", "лев": "льва", "павел": "павла"}
    if lw in exc:
        r = exc[lw]
        return r.upper() if w.isupper() else r.capitalize()
    if lw.endswith("ия"):
        return end("я", "и")
    if lw.endswith("я"):
        return end("я", "и")
    if lw.endswith("а"):
        return end("а", "и" if lw[-2:-1] in "гкхжшщч" else "ы")
    if female:
        return w
    if lw.endswith("й"):
        return end("й", "я")
    if lw.endswith("ь"):
        return end("ь", "я")
    if lw[-1:] in "бвгджзклмнпрстфхцчшщ":
        return end("", "а")
    return w


def fio_genitive(fio):
    """«Петров Пётр Петрович» → «Петрова Петра Петровича» (простые правила, проверяйте результат)."""
    parts = (fio or "").split()
    if len(parts) < 2:
        return fio or ""
    female = len(parts) >= 3 and parts[2].lower().endswith(("вна", "чна", "кызы"))
    if len(parts) == 2:
        female = parts[0].lower().endswith(("ова", "ева", "ина", "ая")) or parts[1].lower().endswith("а")
    kinds = ["surname", "name", "patronymic"]
    return " ".join(_gen_word(w, kinds[i], female) if i < 3 else w for i, w in enumerate(parts))


def fio_short(fio):
    """«Петров Пётр Петрович» → «П.П. Петров»."""
    parts = (fio or "").split()
    if len(parts) < 2:
        return fio or ""
    return "".join(p[0].upper() + "." for p in parts[1:3]) + " " + parts[0]


# ------------------------------------------------------------------ обход документа
def _paragraphs(doc):
    """Все абзацы: тело, таблицы (в т.ч. вложенные), колонтитулы."""
    def from_container(c):
        for p in c.paragraphs:
            yield p
        for t in getattr(c, "tables", []):
            for row in t.rows:
                for cell in row.cells:
                    yield from from_container(cell)
    yield from from_container(doc)
    for s in doc.sections:
        for part in (s.header, s.footer, s.first_page_header, s.first_page_footer,
                     s.even_page_header, s.even_page_footer):
            if part is not None and not part.is_linked_to_previous:
                yield from from_container(part)


def full_text(doc):
    return "\n".join(p.text for p in _paragraphs(doc))


def _sign_paragraph(p):
    """Строка подписи: абзац с полем {{data}} и ещё каким-нибудь полем."""
    keys = [m.group(1) for m in PLACEHOLDER_RE.finditer(p.text)]
    return "data" in keys and len(set(keys)) > 1


def sign_line_keys(data):
    """Поля, которые стоят в строке подписи (кроме даты)."""
    doc = docx.Document(io.BytesIO(data))
    keys = []
    for p in _paragraphs(doc):
        if _sign_paragraph(p):
            for m in PLACEHOLDER_RE.finditer(p.text):
                if m.group(1) != "data" and m.group(1) not in keys:
                    keys.append(m.group(1))
    return keys


def scan(data):
    """Находит поля шаблона. Возвращает список ключей в порядке появления."""
    doc = docx.Document(io.BytesIO(data))
    text = full_text(doc)
    keys = []
    for m in PLACEHOLDER_RE.finditer(text):
        if m.group(1) not in keys:
            keys.append(m.group(1))
    for k, (rx, _, _) in MARKERS.items():
        if rx.search(text):
            keys.append(k)
    return keys


def label(key):
    if key in KNOWN:
        return KNOWN[key][0]
    if key in MARKERS:
        return MARKERS[key][1]
    return key


def field_type(key):
    return KNOWN.get(key, ("", "text", ""))[1]


def hint(key):
    if key in KNOWN:
        return KNOWN[key][2]
    if key in MARKERS:
        return MARKERS[key][2]
    return ""


# ------------------------------------------------------------------ замена
def _replace_in_paragraph(p, rx, repl):
    """Заменяет совпадения rx в абзаце, даже если они разбиты на несколько runs.
    repl — функция(match) → строка. Форматирование берётся из первого run совпадения."""
    count, search_from = 0, 0
    while count < 500:
        runs = p.runs
        texts = [r.text for r in runs]
        joined = "".join(texts)
        m = None
        for mm in rx.finditer(joined, search_from):
            if repl(mm) != mm.group(0):
                m = mm
                break
        if not m or not runs:
            return count
        start, end = m.span()
        new = repl(m)
        # границы runs
        pos, bounds = 0, []
        for t in texts:
            bounds.append((pos, pos + len(t)))
            pos += len(t)
        first = next(i for i, (a, b) in enumerate(bounds) if a <= start < b)
        last = next(i for i, (a, b) in enumerate(bounds) if a < end <= b)
        a0 = bounds[first][0]
        a1 = bounds[last][0]
        if first == last:
            runs[first].text = texts[first][:start - a0] + new + texts[first][end - a0:]
        else:
            runs[first].text = texts[first][:start - a0] + new
            for i in range(first + 1, last):
                runs[i].text = ""
            runs[last].text = texts[last][end - a1:]
        count += 1
        search_from = start + len(new)   # не заменяем внутри только что вставленного текста
    return count


def _fix_sign_tabs(doc, p):
    """В строке подписи «ФИО ⇥⇥⇥…⇥ дата» заменяет цепочку табуляций одной табуляцией
    с выравниванием по правому краю — дата всегда остаётся справа на той же строке."""
    if p.text.count("\t") < 2:
        return
    from docx.enum.text import WD_TAB_ALIGNMENT
    seen = False
    for r in p.runs:
        if "\t" in r.text:
            new = []
            for ch in r.text:
                if ch == "\t":
                    if not seen:
                        new.append(ch)
                        seen = True
                else:
                    new.append(ch)
            r.text = "".join(new)
    sec = doc.sections[0]
    width = sec.page_width - sec.left_margin - sec.right_margin - (p.paragraph_format.right_indent or 0) \
        - (p.paragraph_format.left_indent or 0)
    p.paragraph_format.tab_stops.add_tab_stop(width, WD_TAB_ALIGNMENT.RIGHT)


def fill(data, values, extra=None, sign_values=None):
    """Заполняет шаблон. values — {ключ: текст}; extra — [(найти, заменить), …] дополнительно;
    sign_values — {ключ: текст} для строки подписи (например, «П.П. Петров» вместо родительного падежа).
    Возвращает байты готового .docx."""
    doc = docx.Document(io.BytesIO(data))
    vals = {k: ("" if v is None else str(v)) for k, v in values.items()}

    def ph(m):
        k = m.group(1)
        return vals[k] if k in vals else m.group(0)

    sv = {k: str(v) for k, v in (sign_values or {}).items() if v}

    def ph_sign(m):
        return sv.get(m.group(1), ph(m))

    for p in _paragraphs(doc):
        if _sign_paragraph(p):
            _fix_sign_tabs(doc, p)
        _replace_in_paragraph(p, PLACEHOLDER_RE, ph_sign if sv and _sign_paragraph(p) else ph)
        for k, (rx, _, _) in MARKERS.items():
            if vals.get(k):
                v = vals[k]
                if k == "__passport__" and not v.startswith("("):
                    v = f"({v})"
                _replace_in_paragraph(p, rx, lambda m, v=v: v)
        for find, rep in (extra or []):
            if find:
                _replace_in_paragraph(p, re.compile(re.escape(find)), lambda m, r=rep: r)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def leftovers(data):
    """Незаполненные поля и заглушки в готовом документе."""
    text = full_text(docx.Document(io.BytesIO(data)))
    left = [m.group(0) for m in PLACEHOLDER_RE.finditer(text)]
    for rx, _, _ in MARKERS.values():
        left += [m.group(0)[:40] for m in rx.finditer(text)]
    return left


# ------------------------------------------------------------------ PDF
def soffice():
    return shutil.which("soffice") or shutil.which("libreoffice")


def to_pdf(docx_bytes, timeout=120):
    """Конвертирует .docx в PDF через LibreOffice. Возвращает байты PDF или None, если его нет."""
    exe = soffice()
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "doc.docx")
        with open(src, "wb") as f:
            f.write(docx_bytes)
        # отдельный профиль, чтобы параллельные конвертации не мешали друг другу
        profile = "file://" + os.path.join(tmp, "profile")
        subprocess.run([exe, f"-env:UserInstallation={profile}", "--headless", "--convert-to", "pdf",
                        "--outdir", tmp, src], capture_output=True, timeout=timeout)
        out = os.path.join(tmp, "doc.pdf")
        if not os.path.exists(out):
            raise RuntimeError("LibreOffice не смог сконвертировать документ в PDF")
        with open(out, "rb") as f:
            return f.read()


def signature_spot(pdf_bytes, needles):
    """Ищет строку подписи на последней странице (по тексту из needles, последний найденный).
    Возвращает (номер страницы, x0, y_center, x1, ширина, высота) в пунктах или None."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for pno in range(len(doc) - 1, -1, -1):
            page = doc[pno]
            best = None
            for n in needles:
                if not n:
                    continue
                for r in page.search_for(n.strip()[:60]):
                    if best is None or r.y1 > best.y1:
                        best = r
            if best is not None:
                return pno, best.x0, (best.y0 + best.y1) / 2, best.x1, page.rect.width, page.rect.height
    finally:
        doc.close()
    return None
