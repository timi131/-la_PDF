"""
Веб-версия PDF Объединителя (Streamlit).

Запуск локально:
    pip install -r requirements.txt
    streamlit run app.py

На сервере (порт берётся из переменной PORT, по умолчанию 8501):
    streamlit run app.py --server.address 0.0.0.0 --server.port $PORT
"""
import io
import os
import tempfile
import uuid
import zipfile

import streamlit as st

import pdf_edit as pe
import stamp as stp
from merge_files import SUPPORTED, load_reader, make_groups, merge, parse_ranges, split

st.set_page_config(page_title="PDF Объединитель", page_icon="📄", layout="wide")

TYPES = sorted(e[1:] for e in SUPPORTED)
NUM_POS = {"Снизу по центру": "bottom-center", "Снизу справа": "bottom-right",
           "Снизу слева": "bottom-left", "Сверху по центру": "top-center",
           "Сверху справа": "top-right", "Сверху слева": "top-left"}

ss = st.session_state
if "tmp" not in ss:
    ss.tmp = tempfile.mkdtemp(prefix="pdfapp_")


def save_upload(f):
    """Сохраняет загруженный файл во временную папку, возвращает путь."""
    path = os.path.join(ss.tmp, f"{uuid.uuid4().hex[:8]}_{os.path.basename(f.name)}")
    with open(path, "wb") as out:
        out.write(f.getbuffer())
    return path


def clean_name(name, default):
    name = (name or "").strip() or default
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name[:-4] if name.lower().endswith(".pdf") else name


def human_size(n):
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"


st.title("📄 PDF Объединитель")
st.caption("PDF · JPG · PNG · BMP · GIF · TIFF · WEBP · TXT")

tab_merge, tab_split, tab_edit, tab_stamp = st.tabs(
    ["🔗 Объединить", "✂ Разделить", "✏ Редактор", "🔵 Печать"])

# =============================== ОБЪЕДИНИТЬ ===============================
with tab_merge:
    ss.setdefault("m_files", [])      # [{"name", "path", "size"}]
    ss.setdefault("m_key", 0)
    up = st.file_uploader("Добавьте файлы", type=TYPES, accept_multiple_files=True,
                          key=f"m_up_{ss.m_key}")
    if up:
        for f in up:
            ss.m_files.append({"name": f.name, "path": save_upload(f), "size": f.size})
        ss.m_key += 1
        st.rerun()

    files = ss.m_files
    if not files:
        st.info("Загрузите файлы — их можно будет расставить в нужном порядке.")
    else:
        c1, c2 = st.columns([1, 1])
        if c1.button("А→Я  Сортировать по имени", use_container_width=True):
            files.sort(key=lambda x: x["name"].lower())
            st.rerun()
        if c2.button("🗑 Очистить список", use_container_width=True):
            files.clear()
            st.rerun()

        for i, f in enumerate(files):
            cols = st.columns([0.5, 6, 1.5, 0.7, 0.7, 0.7])
            cols[0].markdown(f"**{i + 1}**")
            cols[1].write(f["name"])
            cols[2].caption(human_size(f["size"]))
            if cols[3].button("▲", key=f"m_up{i}", disabled=i == 0):
                files[i - 1], files[i] = files[i], files[i - 1]
                st.rerun()
            if cols[4].button("▼", key=f"m_dn{i}", disabled=i == len(files) - 1):
                files[i + 1], files[i] = files[i], files[i + 1]
                st.rerun()
            if cols[5].button("✕", key=f"m_del{i}"):
                files.pop(i)
                st.rerun()

        st.divider()
        name = clean_name(st.text_input("Имя итогового файла", "объединённый", key="m_name"),
                          "объединённый")
        if st.button("Объединить в PDF", type="primary", use_container_width=True):
            with st.spinner("Объединение…"):
                try:
                    out = merge([f["path"] for f in files], os.path.join(ss.tmp, "merge_" + name))
                    with open(out, "rb") as fh:
                        ss.m_result = (name + ".pdf", fh.read())
                except Exception as e:
                    st.error(f"Ошибка: {e}")
        if ss.get("m_result"):
            fname, data = ss.m_result
            st.success(f"Готово: {fname} ({human_size(len(data))})")
            st.download_button("⬇ Скачать " + fname, data, fname, "application/pdf",
                               use_container_width=True)

# =============================== РАЗДЕЛИТЬ ===============================
with tab_split:
    f = st.file_uploader("Файл для разделения", type=TYPES, key="s_up")
    if f:
        if ss.get("s_src") != (f.name, f.size):
            ss.s_src = (f.name, f.size)
            ss.s_path = save_upload(f)
            ss.s_total = len(load_reader(ss.s_path).pages)
            ss.pop("s_result", None)
        total = ss.s_total
        st.write(f"**{f.name}** · страниц: **{total}** · {human_size(f.size)}")

        modes = {"Каждая страница — отдельный файл": "each",
                 "По N страниц в файле": "every",
                 "Каждый диапазон — отдельный файл": "ranges",
                 "Извлечь выбранные страницы в один файл": "extract"}
        mode = modes[st.radio("Как разделить", list(modes))]
        value = ""
        if mode == "every":
            value = st.number_input("N страниц", 1, max(total, 1), min(2, total))
        elif mode in ("ranges", "extract"):
            value = st.text_input("Диапазоны", "1-2, 3-" if total > 2 else "1",
                                  help="Например: 1-3, 5, 8- (8- значит с 8-й до конца)")
        fmt = st.radio("Формат результата", ["pdf", "jpg", "png"], horizontal=True,
                       format_func=str.upper)
        prefix = clean_name(st.text_input("Начало имени файлов",
                                          os.path.splitext(f.name)[0]), "страницы")
        try:
            groups = make_groups(total, mode, value)
            n = sum(len(g) for g in groups) if fmt != "pdf" else len(groups)
            st.caption(f"Будет создано файлов: {n}")
            ok = True
        except Exception as e:
            st.error(f"⚠ {e}")
            ok = False

        if st.button("Разделить", type="primary", disabled=not ok, use_container_width=True):
            with st.spinner("Разделение…"):
                try:
                    out_dir = tempfile.mkdtemp(dir=ss.tmp)
                    res = split(ss.s_path, out_dir, groups, prefix, fmt)
                    if len(res) == 1:
                        with open(res[0], "rb") as fh:
                            ss.s_result = (os.path.basename(res[0]), fh.read(), len(res))
                    else:
                        buf = io.BytesIO()
                        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                            for p in res:
                                z.write(p, os.path.basename(p))
                        ss.s_result = (prefix + ".zip", buf.getvalue(), len(res))
                except Exception as e:
                    st.error(f"Ошибка: {e}")
        if ss.get("s_result"):
            fname, data, cnt = ss.s_result
            st.success(f"Создано файлов: {cnt}")
            mime = "application/zip" if fname.endswith(".zip") else "application/octet-stream"
            st.download_button("⬇ Скачать " + fname, data, fname, mime, use_container_width=True)
    else:
        st.info("Загрузите PDF (или картинку / TXT), чтобы разделить его.")

# =============================== РЕДАКТОР ===============================
with tab_edit:
    ss.setdefault("e_docs", [])
    ss.setdefault("e_pages", [])
    ss.setdefault("e_undo", [])
    ss.setdefault("e_sel", set())
    ss.setdefault("e_thumbs", {})
    ss.setdefault("e_key", 0)
    ss.setdefault("e_name", "изменённый")

    def push():
        ss.e_undo.append([dict(p) for p in ss.e_pages])
        del ss.e_undo[:-50]

    up = st.file_uploader("Откройте PDF или изображения (новые файлы добавляются в конец)",
                          type=TYPES, accept_multiple_files=True, key=f"e_up_{ss.e_key}")
    if up:
        if ss.e_pages:
            push()
        for f in up:
            try:
                doc = pe.open_doc(save_upload(f))
            except Exception as e:
                st.error(f"{f.name}: {e}")
                continue
            if doc.needs_pass:
                st.error(f"{f.name}: файл защищён паролем — снимите защиту и загрузите снова.")
                continue
            if not ss.e_docs:
                ss.e_name = os.path.splitext(f.name)[0] + "_изменён"
            ss.e_docs.append(doc)
            ss.e_pages += pe.page_entries(len(ss.e_docs) - 1, doc)
        ss.e_key += 1
        st.rerun()

    pages = ss.e_pages
    if not pages:
        st.info("Загрузите файлы, чтобы поворачивать, удалять и переставлять страницы.")
    else:
        # выделение берём из чекбоксов
        sel = sorted(i for i in range(len(pages)) if ss.get(f"e_chk_{i}"))

        def set_sel(indices):
            for i in range(len(ss.e_pages) + 50):
                ss.pop(f"e_chk_{i}", None)
            for i in indices:
                ss[f"e_chk_{i}"] = True

        def act(fn):
            def run():
                s = sorted(i for i in range(len(ss.e_pages)) if ss.get(f"e_chk_{i}"))
                new_sel = fn(s)
                set_sel(new_sel if new_sel is not None else s)
            return run

        def rotate(a):
            def f(s):
                if s:
                    push()
                    for i in s:
                        ss.e_pages[i]["r"] = (ss.e_pages[i]["r"] + a) % 360
            return f

        def delete(s):
            if s:
                push()
                ss.e_pages = [p for i, p in enumerate(ss.e_pages) if i not in s]
                return []

        def duplicate(s):
            if s:
                push()
                pos = s[-1] + 1
                ss.e_pages[pos:pos] = [dict(ss.e_pages[i]) for i in s]
                return list(range(pos, pos + len(s)))

        def blank(s):
            push()
            pos = s[-1] + 1 if s else len(ss.e_pages)
            ref = ss.e_pages[pos - 1] if pos > 0 else None
            size = pe.page_size(ss.e_docs, ref) if ref else pe.A4
            ss.e_pages.insert(pos, {"d": None, "p": 0, "r": 0, "size": size})
            return [pos]

        def move(d):
            def f(s):
                if not s or (d < 0 and s[0] == 0) or (d > 0 and s[-1] == len(ss.e_pages) - 1):
                    return
                push()
                block = [ss.e_pages[i] for i in s]
                rest = [p for i, p in enumerate(ss.e_pages) if i not in s]
                target = s[0] + d
                ss.e_pages = rest[:target] + block + rest[target:]
                return list(range(target, target + len(block)))
            return f

        def reverse(s):
            push()
            ss.e_pages.reverse()
            n = len(ss.e_pages)
            return [n - 1 - i for i in s]

        def undo(s):
            if ss.e_undo:
                ss.e_pages = ss.e_undo.pop()
                return [i for i in s if i < len(ss.e_pages)]

        def clear_all(s):
            for d in ss.e_docs:
                d.close()
            ss.e_docs, ss.e_pages, ss.e_undo, ss.e_thumbs = [], [], [], {}
            ss.pop("e_result", None)
            return []

        n = len(pages)
        b = st.columns(9)
        b[0].button("⟲ 90°", on_click=act(rotate(-90)), disabled=not sel, use_container_width=True)
        b[1].button("⟳ 90°", on_click=act(rotate(90)), disabled=not sel, use_container_width=True)
        b[2].button("180°", on_click=act(rotate(180)), disabled=not sel, use_container_width=True)
        b[3].button("◀", on_click=act(move(-1)), disabled=not sel, use_container_width=True)
        b[4].button("▶", on_click=act(move(1)), disabled=not sel, use_container_width=True)
        b[5].button("⧉ Копия", on_click=act(duplicate), disabled=not sel, use_container_width=True)
        b[6].button("✕ Удалить", on_click=act(delete), disabled=not sel, use_container_width=True)
        b[7].button("□ Пустая", on_click=act(blank), use_container_width=True)
        b[8].button("↶ Отменить", on_click=act(undo), disabled=not ss.e_undo,
                    use_container_width=True)

        b = st.columns(6)
        b[0].button("Выделить все", on_click=lambda: set_sel(range(n)), use_container_width=True)
        b[1].button("Нечётные", on_click=lambda: set_sel(range(0, n, 2)), use_container_width=True)
        b[2].button("Чётные", on_click=lambda: set_sel(range(1, n, 2)), use_container_width=True)
        b[3].button("Снять выделение", on_click=lambda: set_sel([]), use_container_width=True)
        b[4].button("⇅ Обратный порядок", on_click=act(reverse), use_container_width=True)
        b[5].button("🗑 Закрыть всё", on_click=act(clear_all), use_container_width=True)

        rot = sum(1 for p in pages if p["r"])
        st.caption(f"Страниц: {n} · выделено: {len(sel)}" + (f" · повёрнуто: {rot}" if rot else "")
                   + " — отметьте страницы галочками, затем нажмите действие")

        ncols = 6
        for start in range(0, n, ncols):
            cols = st.columns(ncols)
            for i in range(start, min(start + ncols, n)):
                p = pages[i]
                key = (p["d"], p["p"], p["r"], p.get("size"))
                if key not in ss.e_thumbs:
                    img = pe.render_thumb(ss.e_docs, p, 180)
                    buf = io.BytesIO()
                    img.save(buf, "PNG")
                    ss.e_thumbs[key] = buf.getvalue()
                with cols[i - start]:
                    st.image(ss.e_thumbs[key], use_container_width=True)
                    label = f"{i + 1}"
                    if p["r"]:
                        label += f"  ↻{p['r']}°"
                    if p["d"] is None:
                        label += "  (пустая)"
                    st.checkbox(label, key=f"e_chk_{i}")

        st.divider()
        st.subheader("Сохранение")
        o1, o2 = st.columns(2)
        with o1:
            wm_on = st.checkbox("Водяной знак")
            wm_text = st.text_input("Текст водяного знака", "ЧЕРНОВИК", disabled=not wm_on)
            wm_op = st.slider("Прозрачность, %", 5, 100, 20, disabled=not wm_on)
            num_on = st.checkbox("Номера страниц")
            num_fmt = st.selectbox("Формат номера", ["Стр. {n} из {total}", "{n}", "{n} / {total}",
                                                     "- {n} -"], disabled=not num_on)
            num_pos = st.selectbox("Положение", list(NUM_POS), disabled=not num_on)
        with o2:
            pw_on = st.checkbox("Пароль на открытие")
            pw = st.text_input("Пароль", type="password", disabled=not pw_on)
            compress = st.checkbox("Сжать файл", True)
            name = clean_name(st.text_input("Имя файла", key="e_name"), "изменённый")

        if st.button("Сохранить PDF", type="primary", use_container_width=True):
            if pw_on and not pw:
                st.error("Введите пароль или снимите галочку «Пароль».")
            else:
                with st.spinner("Сохранение…"):
                    try:
                        out = pe.build_pdf(
                            ss.e_docs, ss.e_pages, os.path.join(ss.tmp, "edit_" + name),
                            watermark=wm_text.strip() if wm_on and wm_text.strip() else None,
                            wm_opacity=wm_op / 100,
                            numbers=num_fmt if num_on else None,
                            number_pos=NUM_POS[num_pos],
                            password=pw if pw_on else None, compress=compress)
                        with open(out, "rb") as fh:
                            ss.e_result = (name + ".pdf", fh.read())
                    except Exception as e:
                        st.error(f"Ошибка: {e}")
        if ss.get("e_result"):
            fname, data = ss.e_result
            st.success(f"Готово: {fname} ({human_size(len(data))})")
            st.download_button("⬇ Скачать " + fname, data, fname, "application/pdf",
                               use_container_width=True)

# =============================== ПЕЧАТЬ ===============================
@st.cache_data(show_spinner=False, max_entries=64)
def make_stamp_png(kind, params):
    p = dict(params)
    img = stp.round_stamp(**p) if kind == "round" else stp.rect_stamp(**p)
    return stp.to_png_bytes(img)


with tab_stamp:
    left, right = st.columns([1.15, 1])
    with left:
        kind = st.radio("Вид", ["Круглая печать", "Прямоугольный штамп"], horizontal=True)
        if kind == "Круглая печать":
            outer = st.text_input("Текст по кругу", 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ «НАЗВАНИЕ»')
            bottom = st.text_input("Текст внизу круга (необязательно)", "",
                                   help="Если заполнено — верхний текст идёт дугой, а этот снизу")
            inner = st.text_input("Внутреннее кольцо (необязательно)", "ИНН 0000000000 • ОГРН 0000000000000")
            center = st.text_area("Текст в центре (каждая строка отдельно)", "г. Москва", height=80)
            c1, c2 = st.columns(2)
            emblem = c1.selectbox("Символ в центре", ["нет", "звезда"])
            separator = c2.selectbox("Разделитель в кольце", [" ★ ", " • ", " ✦ ", "  "],
                                     format_func=lambda x: x.strip() or "пробел")
            c1, c2, c3 = st.columns(3)
            double = c1.checkbox("Двойная рамка", True)
            fill = c2.checkbox("Текст по всему кругу", True)
            serif = c3.checkbox("С засечками", False)
            params = dict(outer_text=outer, bottom_text=bottom, inner_text=inner,
                          center_lines=tuple(center.splitlines()),
                          emblem="star" if emblem == "звезда" else "none",
                          double_border=double, fill_circle=fill, separator=separator, serif=serif)
            kind_key = "round"
        else:
            lines = st.text_area("Текст штампа (каждая строка отдельно)", "ОПЛАЧЕНО\n20.11.2026", height=100)
            c1, c2 = st.columns(2)
            ratio = c1.slider("Ширина : высота", 1.0, 4.0, 2.3, 0.1)
            corner = c2.slider("Скругление углов", 0, 30, 6)
            c1, c2 = st.columns(2)
            double = c1.checkbox("Двойная рамка", True)
            serif = c2.checkbox("С засечками", False)
            params = dict(lines=tuple(lines.splitlines()), width=int(300 * ratio), height=300,
                          double_border=double, corner=corner, serif=serif)
            kind_key = "rect"

        st.markdown("**Оформление**")
        c1, c2, c3 = st.columns(3)
        preset = c1.selectbox("Цвет", ["Синий", "Фиолетовый", "Красный", "Чёрный", "Свой"])
        colors = {"Синий": "#1f3fbf", "Фиолетовый": "#6a2fbf", "Красный": "#c0282d", "Чёрный": "#222222"}
        color = c2.color_picker("Свой цвет", "#1f3fbf", disabled=preset != "Свой") \
            if preset == "Свой" else colors[preset]
        border = c3.slider("Толщина рамки", 0.5, 5.0, 2.2 if kind_key == "round" else 2.0, 0.1)
        c1, c2, c3 = st.columns(3)
        ink = c1.slider("Эффект оттиска", 0.0, 1.0, 0.35, 0.05,
                        help="0 — идеально ровная краска, 1 — сильно потёртый оттиск")
        rotation = c2.slider("Наклон, °", -45, 45, 0)
        opacity = c3.slider("Непрозрачность", 0.3, 1.0, 0.95, 0.05)
        params.update(color=color, border=border, ink=ink, rotation=rotation, opacity=opacity)
        if kind_key == "round":
            params["size"] = 600

    try:
        png = make_stamp_png(kind_key, tuple(sorted(params.items())))
    except Exception as e:
        png = None
        right.error(f"Ошибка: {e}")
    with right:
        if png:
            from PIL import Image as _Img
            im = _Img.open(io.BytesIO(png))
            paper = _Img.new("RGBA", (im.width + 40, im.height + 40), (255, 255, 255, 255))
            paper.alpha_composite(im, (20, 20))
            st.image(paper, caption="Так печать выглядит на бумаге", use_container_width=True)
            st.download_button("⬇ Скачать PNG (прозрачный фон)", png, "печать.png", "image/png",
                               use_container_width=True)

    st.divider()
    st.subheader("Поставить печать на PDF")
    pdf = st.file_uploader("Документ", type=TYPES, key="stamp_pdf")
    if pdf and png:
        if ss.get("st_src") != (pdf.name, pdf.size):
            ss.st_src = (pdf.name, pdf.size)
            ss.st_path = save_upload(pdf)
            ss.st_total = len(load_reader(ss.st_path).pages)
            ss.pop("st_result", None)
        total = ss.st_total
        c1, c2, c3 = st.columns(3)
        where = c1.selectbox("Страницы", ["Последняя", "Первая", "Все", "Указать…"])
        rng = c1.text_input("Номера страниц", "1", disabled=where != "Указать…",
                            help="Например: 1, 3-5")
        pos_name = c2.selectbox("Положение", list(stp.POSITIONS) + ["Своё"])
        if pos_name == "Своё":
            x = c2.slider("По горизонтали, %", 0, 100, 75) / 100
            y = c2.slider("По вертикали, %", 0, 100, 85) / 100
        else:
            x, y = stp.POSITIONS[pos_name]
        width_mm = c3.slider("Диаметр / ширина, мм", 15, 120, 40 if kind_key == "round" else 50)
        try:
            if where == "Последняя":
                pages = [total - 1]
            elif where == "Первая":
                pages = [0]
            elif where == "Все":
                pages = list(range(total))
            else:
                pages = sorted({i for g in parse_ranges(rng, total) for i in g})
            ok = True
        except Exception as e:
            st.error(f"⚠ {e}")
            ok = False

        if ok:
            # предпросмотр первой выбранной страницы
            prev_path = os.path.join(ss.tmp, "stamp_preview.pdf")
            stp.apply_stamp(ss.st_path, png, prev_path, pages=[pages[0]], x=x, y=y, width_mm=width_mm)
            pv = pe.fitz.open(prev_path)
            pix = pv[pages[0]].get_pixmap(dpi=70)
            pv.close()
            pc1, pc2 = st.columns([1, 1.4])
            pc1.image(pix.tobytes("png"), caption=f"Предпросмотр: страница {pages[0] + 1} из {total}",
                      use_container_width=True)
            with pc2:
                name = clean_name(st.text_input("Имя файла",
                                                os.path.splitext(pdf.name)[0] + "_с_печатью"),
                                  "с_печатью")
                if st.button("Поставить печать", type="primary", use_container_width=True):
                    with st.spinner("Обработка…"):
                        out = stp.apply_stamp(ss.st_path, png, os.path.join(ss.tmp, "stamped_" + name),
                                              pages=pages, x=x, y=y, width_mm=width_mm)
                        with open(out, "rb") as fh:
                            ss.st_result = (name + ".pdf", fh.read())
                if ss.get("st_result"):
                    fname, data = ss.st_result
                    st.success(f"Готово: {fname} — печать на {len(pages)} стр.")
                    st.download_button("⬇ Скачать " + fname, data, fname, "application/pdf",
                                       use_container_width=True)
    elif not pdf:
        st.caption("Загрузите PDF (или картинку), чтобы поставить на него печать.")
