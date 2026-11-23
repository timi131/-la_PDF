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
import datetime as dt

import docgen
import orgdata
import signature as sgn
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

tab_quick, tab_merge, tab_split, tab_edit, tab_stamp, tab_sign, tab_docs = st.tabs(
    ["⚡ Документ по ИНН", "🔗 Объединить", "✂ Разделить", "✏ Редактор", "🔵 Печать", "✍ Подпись",
     "📄 Документы"])


# ---------------------- общий блок «Поставить на PDF» (печать и/или подпись) ----------------------
PLACE_ITEMS = {"stamp": ("Печать", 40, "Снизу справа"), "sign": ("Подпись", 45, "Рядом с печатью")}
PLACE_POS = dict(stp.POSITIONS, **{"Рядом с печатью": (0.6, 0.83)})


def preview_on_paper(png, caption):
    from PIL import Image as _Img
    im = _Img.open(io.BytesIO(png))
    paper = _Img.new("RGBA", (im.width + 40, im.height + 40), (255, 255, 255, 255))
    paper.alpha_composite(im, (20, 20))
    st.image(paper, caption=caption, use_container_width=True)


def place_on_pdf_ui(prefix, primary):
    """Блок постановки печати/подписи на PDF. prefix — уникальный префикс ключей."""
    st.subheader("Поставить на PDF")
    available = [k for k in PLACE_ITEMS if ss.get("png_" + k)]
    if not available:
        return
    what = st.multiselect("Что поставить", available, default=[primary] if primary in available else [],
                          format_func=lambda k: PLACE_ITEMS[k][0], key=prefix + "_what",
                          help="Можно поставить печать и подпись одновременно")
    if "stamp" not in available or "sign" not in available:
        other = "подпись (вкладка «✍ Подпись»)" if primary == "stamp" else "печать (вкладка «🔵 Печать»)"
        st.caption(f"Чтобы поставить ещё и {other}, сначала создайте её на соседней вкладке.")
    pdf = st.file_uploader("Документ", type=TYPES, key=prefix + "_pdf")
    if not pdf:
        st.caption("Загрузите PDF (или картинку), на который нужно поставить печать или подпись.")
        return
    if not what:
        st.info("Выберите, что поставить на документ.")
        return
    src_key = prefix + "_src"
    if ss.get(src_key) != (pdf.name, pdf.size):
        ss[src_key] = (pdf.name, pdf.size)
        ss[prefix + "_path"] = save_upload(pdf)
        ss[prefix + "_total"] = len(load_reader(ss[prefix + "_path"]).pages)
        ss.pop(prefix + "_result", None)
    path, total = ss[prefix + "_path"], ss[prefix + "_total"]

    c1, c2 = st.columns([1, 2])
    where = c1.selectbox("Страницы", ["Последняя", "Первая", "Все", "Указать…"], key=prefix + "_where")
    rng = c2.text_input("Номера страниц", "1", disabled=where != "Указать…", key=prefix + "_rng",
                        help="Например: 1, 3-5")
    try:
        pages = {"Последняя": [total - 1], "Первая": [0], "Все": list(range(total))}.get(where) \
            or sorted({i for g in parse_ranges(rng, total) for i in g})
    except Exception as e:
        st.error(f"⚠ {e}")
        return

    items = []
    cols = st.columns(len(what))
    for col, k in zip(cols, what):
        title, mm, pos_default = PLACE_ITEMS[k]
        with col:
            st.markdown(f"**{title}**")
            names = list(PLACE_POS) + ["Своё"]
            pos = st.selectbox("Положение", names, index=names.index(pos_default),
                               key=f"{prefix}_{k}_pos")
            if pos == "Своё":
                x = st.slider("По горизонтали, %", 0, 100, int(PLACE_POS[pos_default][0] * 100),
                              key=f"{prefix}_{k}_x") / 100
                y = st.slider("По вертикали, %", 0, 100, int(PLACE_POS[pos_default][1] * 100),
                              key=f"{prefix}_{k}_y") / 100
            else:
                x, y = PLACE_POS[pos]
            width = st.slider("Ширина, мм", 15, 120, mm, key=f"{prefix}_{k}_w")
        items.append({"png": ss["png_" + k], "x": x, "y": y, "width_mm": width, "kind": k})
    # подпись кладём поверх печати, как на настоящих документах
    items.sort(key=lambda it: it["kind"] == "sign")

    prev_path = os.path.join(ss.tmp, prefix + "_preview.pdf")
    stp.apply_images(path, items, prev_path, pages=[pages[0]])
    pv = pe.fitz.open(prev_path)
    pix = pv[pages[0]].get_pixmap(dpi=70)
    pv.close()
    pc1, pc2 = st.columns([1, 1.4])
    pc1.image(pix.tobytes("png"), caption=f"Предпросмотр: страница {pages[0] + 1} из {total}",
              use_container_width=True)
    with pc2:
        suffix = "_подписано" if "sign" in what else "_с_печатью"
        name = clean_name(st.text_input("Имя файла", os.path.splitext(pdf.name)[0] + suffix,
                                        key=prefix + "_name"), "документ")
        if st.button("Поставить на документ", type="primary", use_container_width=True,
                     key=prefix + "_go"):
            with st.spinner("Обработка…"):
                out = stp.apply_images(path, items, os.path.join(ss.tmp, prefix + "_" + name),
                                       pages=pages)
                with open(out, "rb") as fh:
                    ss[prefix + "_result"] = (name + ".pdf", fh.read(), len(pages))
        if ss.get(prefix + "_result"):
            fname, data, n = ss[prefix + "_result"]
            st.success(f"Готово: {fname} — на {n} стр.")
            st.download_button("⬇ Скачать " + fname, data, fname, "application/pdf",
                               use_container_width=True, key=prefix + "_dl")

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


def dadata_key():
    """Ключ из текущей сессии (введён в приложении) или из переменной окружения сервера."""
    return ss.get("dadata_key") or orgdata.api_key()


def save_user_key(input_key="dadata_key_input", msg_key="key_msg"):
    """Проверяет введённый ключ тестовым запросом и сохраняет его только в этой сессии."""
    key = (ss.get(input_key) or "").strip()
    ss.pop(msg_key, None)
    if not key:
        ss[msg_key] = ("error", "Вставьте ключ.")
        return
    try:
        orgdata.find_by_inn("7707083893", key=key)   # тестовый запрос (ИНН ПАО Сбербанк)
    except orgdata.OrgLookupError as e:
        ss[msg_key] = ("error", str(e))
        return
    ss.dadata_key = key
    ss[input_key] = ""
    ss[msg_key] = ("success", "Ключ работает и сохранён до закрытия вкладки.")


def forget_user_key():
    ss.pop("dadata_key", None)
    ss.key_msg = ("info", "Ключ удалён из сессии.")


def org_lookup():
    """Заполняет поля печати и подписи реквизитами по ИНН (DaData)."""
    ss.pop("org_details", None)
    ss.pop("org_error", None)
    try:
        item = orgdata.find_by_inn(ss.get("org_inn", ""), key=dadata_key())
    except orgdata.OrgLookupError as e:
        ss.org_error = str(e)
        return
    if not item:
        ss.org_error = "Организация с таким ИНН не найдена."
        return
    apply_org(orgdata.to_details(item))


def apply_org(det):
    """Подставляет реквизиты организации сразу везде: печать, подпись, поля документов."""
    f = orgdata.stamp_fields(det)
    ss.st_kind = "Круглая печать"
    ss.st_outer, ss.st_inner, ss.st_center, ss.st_bottom = f["outer"], f["inner"], f["center"], ""
    sig = orgdata.signature_text(det)
    if sig:
        ss.sg_text = sig
    ss.org_details = det
    ss.doc_f_org_name = det["short_name"]
    ss.doc_f_inn = det["inn"]
    ss.doc_sign_org_name = det["short_name"]
    if det["manager"]:
        ss.doc_f_director = docgen.fio_genitive(det["manager"])
        ss.doc_sign_director = docgen.fio_short(det["manager"])
        ss.doc_f_director_short = docgen.fio_short(det["manager"])


with tab_stamp:
    with st.expander("🔎 Заполнить реквизиты по ИНН своей организации", expanded=False):
        # --- ключ DaData
        if ss.get("dadata_key"):
            k = ss.dadata_key
            c1, c2 = st.columns([2, 1])
            c1.success(f"🔑 Используется ваш ключ DaData: {k[:4]}…{k[-4:]}")
            c2.button("Забыть ключ", on_click=forget_user_key, use_container_width=True)
        else:
            if orgdata.api_key():
                st.caption("🔑 Ключ DaData уже подключён. При желании можно указать свой ниже.")
            else:
                st.info("Для поиска нужен бесплатный ключ DaData: зарегистрируйтесь на "
                        "[dadata.ru](https://dadata.ru), откройте «Профиль» и скопируйте «API-ключ».")
            c1, c2 = st.columns([2, 1])
            c1.text_input("API-ключ DaData", key="dadata_key_input", type="password",
                          placeholder="Вставьте API-ключ DaData", label_visibility="collapsed")
            c2.button("Сохранить ключ", on_click=save_user_key, use_container_width=True)
            st.caption("Ключ хранится только в этой вкладке браузера, на сервер не записывается "
                       "и пропадёт после её закрытия.")
        if ss.get("key_msg"):
            kind_, text_ = ss.key_msg
            getattr(st, kind_)(text_)
        has_key = bool(dadata_key())

        # --- поиск
        c1, c2 = st.columns([2, 1])
        c1.text_input("ИНН", key="org_inn", placeholder="10 цифр — организация, 12 — ИП",
                      label_visibility="collapsed", disabled=not has_key)
        c2.button("Найти и заполнить", on_click=org_lookup, use_container_width=True,
                  disabled=not has_key)
        st.caption("Данные берутся из ЕГРЮЛ/ЕГРИП через DaData. Название, ИНН/ОГРН и город "
                   "подставятся в печать, «И. Фамилия» руководителя — в подпись.")
        if ss.get("org_error"):
            st.error(ss.org_error)
        det = ss.get("org_details")
        if det:
            msg = (f"**{det['short_name']}**  \nИНН {det['inn']}"
                   + (f" · КПП {det['kpp']}" if det["kpp"] else "")
                   + f" · {'ОГРНИП' if det['type'] == 'ИП' else 'ОГРН'} {det['ogrn']}  \n{det['address']}"
                   + (f"  \n{det['manager_post']}: {det['manager']}" if det["manager"] else ""))
            if det["status"] and det["status"] != "действующая":
                st.warning(f"Статус: {det['status']}.  \n" + msg)
            else:
                st.success(msg)

    ss.setdefault("st_outer", "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ «НАЗВАНИЕ»")
    ss.setdefault("st_bottom", "")
    ss.setdefault("st_inner", "ИНН 0000000000 • ОГРН 0000000000000")
    ss.setdefault("st_center", "г. Москва")
    left, right = st.columns([1.15, 1])
    with left:
        kind = st.radio("Вид", ["Круглая печать", "Прямоугольный штамп"], horizontal=True, key="st_kind")
        if kind == "Круглая печать":
            outer = st.text_input("Текст по кругу", key="st_outer")
            bottom = st.text_input("Текст внизу круга (необязательно)", key="st_bottom",
                                   help="Если заполнено — верхний текст идёт дугой, а этот снизу")
            inner = st.text_input("Внутреннее кольцо (необязательно)", key="st_inner")
            center = st.text_area("Текст в центре (каждая строка отдельно)", key="st_center", height=80)
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
        border_style = st.radio("Рамка", ["Сплошная", "Прерывистая (пунктир)"], horizontal=True)

        st.markdown("**Потёртость и прозрачность**")
        WEAR_PRESETS = {
            "Новая (чистая)": (0.0, 0.0, 0.0, 0),
            "Обычный оттиск": (0.15, 0.0, 0.3, 5),
            "Слегка потёртая": (0.3, 0.1, 0.35, 5),
            "Потёртая": (0.45, 0.25, 0.4, 10),
            "Старая, прерывистая": (0.55, 0.4, 0.5, 15),
        }
        ss.setdefault("st_wear", 0.15)
        ss.setdefault("st_breaks", 0.0)
        ss.setdefault("st_uneven", 0.3)
        ss.setdefault("st_transp", 5)
        ss.setdefault("st_seed", 1)

        def apply_preset():
            w, b_, u, t = WEAR_PRESETS[ss.st_preset]
            ss.st_wear, ss.st_breaks, ss.st_uneven, ss.st_transp = w, b_, u, t

        def new_seed():
            ss.st_seed = (ss.st_seed % 999) + 1

        c1, c2 = st.columns([2, 1])
        c1.selectbox("Готовый вариант", list(WEAR_PRESETS), index=1, key="st_preset",
                     on_change=apply_preset)
        c2.button("🎲 Другой узор", on_click=new_seed, use_container_width=True,
                  help="Сгенерировать другое случайное расположение потёртостей")
        c1, c2 = st.columns(2)
        wear = c1.slider("Потёртость", 0.0, 1.0, step=0.05, key="st_wear",
                         help="Мелкие пятнышки и зерно без краски")
        breaks = c2.slider("Прерывистость", 0.0, 1.0, step=0.05, key="st_breaks",
                           help="Разрывы линий и букв, непропечатанные участки, царапины")
        c1, c2 = st.columns(2)
        uneven = c1.slider("Неравномерный нажим", 0.0, 1.0, step=0.05, key="st_uneven",
                           help="Одна сторона печати бледнее, краска ложится пятнами")
        transp = c2.slider("Прозрачность, %", 0, 80, step=5, key="st_transp",
                           help="0 — насыщенный цвет, 80 — едва заметный оттиск")
        c1, c2 = st.columns(2)
        rotation = c1.slider("Наклон, °", -45, 45, 0)
        seed = c2.number_input("Номер узора", 1, 999, key="st_seed",
                               help="Один и тот же номер даёт одинаковый узор потёртостей")
        params.update(color=color, border=border, rotation=rotation, opacity=1 - transp / 100,
                      wear=wear, breaks=breaks, uneven=uneven, seed=int(seed),
                      dashed=border_style.startswith("Прерывистая"))
        if kind_key == "round":
            params["size"] = 600

    try:
        png = make_stamp_png(kind_key, tuple(sorted(params.items())))
        ss.png_stamp = png
    except Exception as e:
        png = None
        right.error(f"Ошибка: {e}")
    with right:
        if png:
            preview_on_paper(png, "Так печать выглядит на бумаге")
            st.download_button("⬇ Скачать PNG (прозрачный фон)", png, "печать.png", "image/png",
                               use_container_width=True)

    st.divider()
    place_on_pdf_ui("pl_stamp", "stamp")


# =============================== ПОДПИСЬ ===============================
@st.cache_data(show_spinner=False, max_entries=64)
def make_sign_png(params):
    return sgn.to_png_bytes(sgn.signature_from_text(**dict(params)))


@st.cache_data(show_spinner=False, max_entries=16)
def clean_photo_png(data, params):
    return sgn.to_png_bytes(sgn.signature_from_photo(data, **dict(params)))


with tab_sign:
    st.caption("Создавайте и ставьте только свою собственную подпись. "
               "Подделка чужой подписи незаконна.")
    left, right = st.columns([1.15, 1])
    with left:
        src = st.radio("Способ", ["Из имени (рукописный шрифт)", "Из фото моей подписи"], horizontal=True)
        ss.setdefault("sg_seed", 1)
        sign_png = None
        if src.startswith("Из имени"):
            ss.setdefault("sg_text", "Иванов")
            text = st.text_input("Текст подписи", key="sg_text", help="Фамилия, «И. Иванов» или инициалы")
            c1, c2 = st.columns(2)
            font = c1.selectbox("Почерк", list(sgn.FONTS))
            flourish = c2.selectbox("Росчерк", list(sgn.FLOURISHES), index=1)
            c1, c2 = st.columns(2)
            mode = c1.radio("Написание", ["Полностью", "Первые буквы + росчерк"])
            keep = c2.slider("Сколько букв оставить", 1, 6, 3,
                             disabled=mode == "Полностью")
            c1, c2, c3 = st.columns(3)
            preset = c1.selectbox("Цвет чернил", ["Синий", "Тёмно-синий", "Фиолетовый", "Чёрный", "Свой"])
            colors = {"Синий": "#1b3fa8", "Тёмно-синий": "#1b2a6f", "Фиолетовый": "#4b2a9f",
                      "Чёрный": "#1a1a1a"}
            color = c2.color_picker("Свой цвет", "#1b2a8f") if preset == "Свой" else colors[preset]
            slant = c3.slider("Наклон, °", -10, 35, 12)
            c1, c2, c3 = st.columns(3)
            thickness = c1.slider("Толщина", 0.0, 1.0, 0.2, 0.05)
            jitter = c2.slider("Живость руки", 0.0, 1.0, 0.35, 0.05,
                               help="Разный наклон, размер и высота букв — как при письме от руки")
            pressure = c3.slider("Неровный нажим", 0.0, 1.0, 0.3, 0.05)
            c1, c2 = st.columns([1, 1])
            c1.button("🎲 Другой вариант", use_container_width=True,
                      on_click=lambda: ss.update(sg_seed=ss.sg_seed % 999 + 1))
            seed = c2.number_input("Номер варианта", 1, 999, key="sg_seed")
            params = dict(text=text, font=font, flourish=sgn.FLOURISHES[flourish],
                          mode="full" if mode == "Полностью" else "short", keep_letters=keep,
                          color=color, slant=slant, thickness=thickness, jitter=jitter,
                          pressure=pressure, seed=int(seed), size=110)
            try:
                sign_png = make_sign_png(tuple(sorted(params.items())))
            except Exception as e:
                st.error(f"Ошибка: {e}")
        else:
            photo = st.file_uploader("Фото или скан подписи на белой бумаге",
                                     type=["jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff"],
                                     key="sg_photo")
            st.caption("Совет: распишитесь ручкой на чистом листе и сфотографируйте при хорошем свете.")
            c1, c2 = st.columns(2)
            sens = c1.slider("Чувствительность", 0.0, 1.0, 0.5, 0.05,
                             help="Больше — сохраняются тонкие и светлые штрихи; меньше — убирается грязь")
            keep_color = c2.checkbox("Оставить исходный цвет чернил", False)
            preset = c2.selectbox("Цвет чернил", ["Синий", "Тёмно-синий", "Фиолетовый", "Чёрный"],
                                  disabled=keep_color)
            colors = {"Синий": "#1b3fa8", "Тёмно-синий": "#1b2a6f", "Фиолетовый": "#4b2a9f",
                      "Чёрный": "#1a1a1a"}
            if photo:
                try:
                    sign_png = clean_photo_png(photo.getvalue(), tuple(sorted(dict(
                        color=colors[preset], keep_color=keep_color, sensitivity=sens).items())))
                except Exception as e:
                    st.error(f"⚠ {e}")
        ss.png_sign = sign_png or ss.get("png_sign")
    with right:
        if sign_png:
            preview_on_paper(sign_png, "Так подпись выглядит на бумаге")
            st.download_button("⬇ Скачать PNG (прозрачный фон)", sign_png, "подпись.png", "image/png",
                               use_container_width=True)
        else:
            st.info("Здесь появится подпись.")
    st.divider()
    place_on_pdf_ui("pl_sign", "sign")


# =============================== ДОКУМЕНТЫ ПО ШАБЛОНАМ ===============================
def doc_lookup():
    """Заполняет поля документа по ИНН через DaData."""
    ss.pop("doc_org_error", None)
    try:
        item = orgdata.find_by_inn(ss.get("doc_inn", ""), key=dadata_key())
    except orgdata.OrgLookupError as e:
        ss.doc_org_error = str(e)
        return
    if not item:
        ss.doc_org_error = "Организация с таким ИНН не найдена."
        return
    det = orgdata.to_details(item)
    apply_org(det)
    ss.doc_org_ok = f"Заполнено: {det['short_name']}" + (
        f" · статус: {det['status']}" if det["status"] and det["status"] != "действующая" else "")


def header_ui(prefix):
    """Блок «Шапка документа». Возвращает (включена, кому, заголовок)."""
    st.markdown("**Шапка документа**")
    c1, c2, c3 = st.columns([0.8, 2, 1.4])
    on = c1.checkbox("Добавить", True, key=prefix + "_hdr_on")
    opts = docgen.OPERATORS + ["Свой вариант…"]
    to = c2.selectbox("Кому", opts, key=prefix + "_hdr_to", disabled=not on)
    if to == "Свой вариант…":
        to = c2.text_input("Кому (свой текст)", key=prefix + "_hdr_to_own", disabled=not on,
                           placeholder="Например: Оператору мобильной связи ПАО «МегаФон»")
    title = c3.text_input("Заголовок", docgen.DEFAULT_TITLE, key=prefix + "_hdr_title", disabled=not on)
    return on, to, title


def decline_director(nom):
    """«Петров Пётр Петрович» → в тексте «Петрова Петра Петровича», в подписи «П.П. Петров»."""
    ss.doc_f_director = docgen.fio_genitive(nom)
    ss.doc_sign_director = docgen.fio_short(nom)


with tab_docs:
    st.caption("Шаблоны Word (.docx) с полями вида {{org_name}}, {{inn}}, {{director}}, {{srok}}, {{data}}. "
               "Заглушки «ХХХХХХ», «ХХХХХХ.ru» и подсказка про паспортные данные тоже заполняются.")
    c1, c2 = st.columns([1, 2])
    src = c1.radio("Шаблон", ["Встроенный", "Свой файл .docx"], key="doc_src")
    tpl_name, tpl_data = None, None
    if src == "Встроенный":
        files = [f for f in docgen.BUILTIN if os.path.exists(os.path.join(docgen.TEMPLATE_DIR, f))]
        if files:
            f = c2.selectbox("Документ", files, format_func=lambda x: docgen.BUILTIN[x], key="doc_builtin")
            tpl_name = os.path.splitext(f)[0]
            with open(os.path.join(docgen.TEMPLATE_DIR, f), "rb") as fh:
                tpl_data = fh.read()
        else:
            c2.warning("Встроенные шаблоны не найдены в папке templates/.")
    else:
        up = c2.file_uploader("Шаблон .docx", type=["docx"], key="doc_upload")
        if up:
            tpl_name, tpl_data = os.path.splitext(up.name)[0], up.getvalue()

    if tpl_data:
        try:
            keys = docgen.scan(tpl_data)
            sign_keys = docgen.sign_line_keys(tpl_data)
        except Exception as e:
            st.error(f"Не удалось прочитать шаблон: {e}")
            keys = None
        if keys is not None and not keys:
            st.warning("В шаблоне не найдено полей {{…}} — заполнять нечего. "
                       "Добавьте в Word поля вида {{org_name}}, {{inn}}, {{data}}.")
        elif keys:
            # --- автозаполнение по ИНН
            if any(k in keys for k in ("org_name", "inn", "director")):
                with st.expander("🔎 Заполнить по ИНН (DaData)", expanded=False):
                    has_key = bool(dadata_key())
                    if not has_key:
                        st.info("Сначала сохраните ключ DaData на вкладке «🔵 Печать» "
                                "(блок «Заполнить реквизиты по ИНН») или задайте DADATA_API_KEY на сервере.")
                    c1, c2 = st.columns([2, 1])
                    c1.text_input("ИНН", key="doc_inn", placeholder="10 цифр — организация, 12 — ИП",
                                  label_visibility="collapsed", disabled=not has_key)
                    c2.button("Найти и заполнить", key="doc_find", on_click=doc_lookup,
                              use_container_width=True, disabled=not has_key)
                    if ss.get("doc_org_error"):
                        st.error(ss.doc_org_error)
                    elif ss.get("doc_org_ok"):
                        st.success(ss.doc_org_ok + " — проверьте поля ниже, особенно падеж ФИО. "
                                   "Печать и подпись тоже обновлены по этим реквизитам.")

            st.markdown("**Поля документа**")
            today = dt.date.today()
            date_fmt = st.selectbox("Формат дат", list(docgen.DATE_FORMATS), key="doc_date_fmt")
            values, sign_values = {}, {}
            cols = st.columns(2)
            for i, k in enumerate(keys):
                col = cols[i % 2]
                lab, h = docgen.label(k), docgen.hint(k) or None
                if docgen.field_type(k) == "date":
                    default = today if k == "data" else today.replace(year=today.year + 1)
                    d = col.date_input(lab, default, key=f"doc_f_{k}", format="DD.MM.YYYY", help=h)
                    values[k] = docgen.format_date(d, date_fmt)
                else:
                    if k == "director" and "director" in sign_keys:
                        lab = "ФИО руководителя в тексте (родительный падеж)"
                        h = "Как после слов «в лице генерального директора …», например: Петрова Петра Петровича"
                    values[k] = col.text_input(lab, key=f"doc_f_{k}", help=h)
                    if k == "director" and values[k]:
                        col.caption("Нужен родительный падеж. Можно ввести ФИО в обычной форме "
                                    "и нажать кнопку ниже.")
                        col.button("Просклонять ФИО", key="doc_decline", on_click=decline_director,
                                   args=(values[k],))
            # строка подписи
            if sign_keys:
                st.markdown("**Строка подписи** (внизу документа)")
                cols = st.columns(len(sign_keys))
                for col, k in zip(cols, sign_keys):
                    sign_values[k] = col.text_input(
                        f"{docgen.label(k)} — в строке подписи", key=f"doc_sign_{k}",
                        placeholder=values.get(k, ""),
                        help="Если оставить пустым — будет то же, что и в тексте. "
                             "Для руководителя обычно «И.О. Фамилия».")

            d_header = header_ui("doc")
            if d_header[0]:
                org = values.get("org_name", "")
                if "director" in keys:
                    gen = values.get("director", "")
                    auto = [f"От генерального директора {org}".strip(), gen]
                elif "inn" in keys and org:   # ИП
                    fio = org[3:].strip() if org.upper().startswith("ИП ") else org
                    auto = ["От индивидуального предпринимателя", docgen.fio_genitive(fio)]
                else:                          # физлицо
                    auto = [f"От {docgen.fio_genitive(org)}"] if org else []
                auto_txt = "\n".join(x for x in auto if x)
                from_txt = st.text_area("От кого (каждая строка отдельно)", auto_txt, height=70,
                                        key=f"doc_hdr_from_{abs(hash(auto_txt))}",
                                        help="Заполняется автоматически из полей выше; можно поправить.")
            with st.expander("Дополнительные замены (необязательно)"):
                extra_txt = st.text_area("Каждая строка: что найти => на что заменить", key="doc_extra",
                                         placeholder="ООО «ПРОМОДРАЙВ» => ООО «Другая компания»", height=90)
            extra = []
            for line in (extra_txt or "").splitlines():
                if "=>" in line:
                    a_, b_ = line.split("=>", 1)
                    if a_.strip():
                        extra.append((a_.strip(), b_.strip()))

            empty = [docgen.label(k) for k in keys if not str(values.get(k, "")).strip()]
            if empty:
                st.warning("Не заполнено: " + ", ".join(empty))

            st.markdown("**Печать и подпись**")
            have = [k for k in ("stamp", "sign") if ss.get("png_" + k)]
            if not docgen.soffice():
                st.caption("PDF недоступен: на сервере нет LibreOffice — будет только DOCX.")
                put = []
            elif have:
                names = {"stamp": "печать (вкладка «🔵 Печать»)", "sign": "подпись (вкладка «✍ Подпись»)"}
                put = st.multiselect("Поставить в PDF у строки подписи", have, default=have,
                                     format_func=lambda k: names[k], key="doc_put")
            else:
                st.caption("Чтобы в PDF сразу стояли печать и подпись, сначала создайте их "
                           "на вкладках «🔵 Печать» и «✍ Подпись».")
                put = []

            out_name = clean_name(st.text_input("Имя файла", f"{tpl_name}_{today.strftime('%d.%m.%Y')}",
                                                key="doc_out_name"), "документ")
            if st.button("Создать документ", type="primary", use_container_width=True, key="doc_go"):
                with st.spinner("Создание документа…"):
                    try:
                        docx_bytes = docgen.fill(tpl_data, values, extra, sign_values)
                        if d_header[0]:
                            docx_bytes = docgen.add_header(docx_bytes, d_header[1],
                                                           [x for x in from_txt.splitlines() if x.strip()],
                                                           d_header[2])
                        pdf_bytes = docgen.to_pdf(docx_bytes) if docgen.soffice() else None
                        signed = None
                        if pdf_bytes and put:
                            needles = [sign_values.get(k) or values.get(k) for k in sign_keys] + [values.get("data")]
                            spot = docgen.signature_spot(pdf_bytes, needles)
                            src_pdf = os.path.join(ss.tmp, "doc_src.pdf")
                            with open(src_pdf, "wb") as fh:
                                fh.write(pdf_bytes)
                            if spot:
                                pno, x0, yc, x1, pw, ph = spot
                                # подпись — сразу справа от ФИО, печать — по центру строки, чуть выше
                                pos = {"sign": ((x1 + 75) / pw, (yc - 6) / ph, 40),
                                       "stamp": (0.5, (yc - 25) / ph, 40)}
                                pages = [pno]
                            else:
                                pos = {"sign": (0.45, 0.85, 40), "stamp": (0.55, 0.83, 40)}
                                pages = None
                            items = [{"png": ss["png_" + k], "x": pos[k][0], "y": pos[k][1],
                                      "width_mm": pos[k][2]} for k in ("stamp", "sign") if k in put]
                            out_pdf = stp.apply_images(src_pdf, items, os.path.join(ss.tmp, "doc_signed"),
                                                       pages=pages)
                            with open(out_pdf, "rb") as fh:
                                signed = fh.read()
                        ss.doc_result = {"name": out_name, "docx": docx_bytes, "pdf": pdf_bytes,
                                         "signed": signed, "left": docgen.leftovers(docx_bytes)}
                    except Exception as e:
                        st.error(f"Ошибка: {e}")
                        ss.pop("doc_result", None)

            res = ss.get("doc_result")
            if res:
                if res["left"]:
                    st.warning("В документе остались незаполненные места: " + ", ".join(res["left"]))
                else:
                    st.success("Документ готов.")
                show = res["signed"] or res["pdf"]
                pc1, pc2 = st.columns([1, 1.2])
                if show:
                    pv = pe.fitz.open(stream=show, filetype="pdf")
                    pc1.image(pv[len(pv) - 1].get_pixmap(dpi=80).tobytes("png"),
                              caption="Предпросмотр (последняя страница)", use_container_width=True)
                    pv.close()
                with pc2:
                    st.download_button("⬇ Скачать DOCX (Word)", res["docx"], res["name"] + ".docx",
                                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                       use_container_width=True, key="doc_dl_docx")
                    if res["pdf"]:
                        st.download_button("⬇ Скачать PDF", res["pdf"], res["name"] + ".pdf",
                                           "application/pdf", use_container_width=True, key="doc_dl_pdf")
                    if res["signed"]:
                        st.download_button("⬇ Скачать PDF с печатью и подписью", res["signed"],
                                           res["name"] + "_подписано.pdf", "application/pdf",
                                           use_container_width=True, key="doc_dl_signed")


# =============================== ⚡ ДОКУМЕНТ ПО ИНН (всё одной кнопкой) ===============================
QUICK_LOOKS = {  # вид оттиска: (потёртость, прерывистость, нажим, непрозрачность)
    "Обычный оттиск": (0.15, 0.0, 0.3, 0.95),
    "Чистая печать": (0.0, 0.0, 0.0, 1.0),
    "Слегка потёртая": (0.3, 0.1, 0.35, 0.95),
}
QUICK_COLORS = {"Синий": "#1f3fbf", "Фиолетовый": "#6a2fbf", "Чёрный": "#222222"}
FROM_INN = {"org_name", "inn", "director", "director_short"}   # эти поля берутся из реестра


def quick_build(tpl_file, inn, manual, want_stamp, want_sign, look, color, header=None):
    """Ищет организацию по ИНН, заполняет шаблон, делает печать/подпись и PDF."""
    with open(os.path.join(docgen.TEMPLATE_DIR, tpl_file), "rb") as fh:
        tpl = fh.read()
    sign_keys = docgen.sign_line_keys(tpl)
    values, sign_values, det = dict(manual), {}, None
    if inn:
        item = orgdata.find_by_inn(inn, key=dadata_key())
        if not item:
            raise orgdata.OrgLookupError("Организация с таким ИНН не найдена.")
        det = orgdata.to_details(item)
        values["inn"] = det["inn"]
        if det["type"] == "ИП":
            values["org_name"] = det["short_name"]
            sign_values["org_name"] = docgen.fio_short(det["manager"])
        else:
            values["org_name"] = det["short_name"]
            sign_values["org_name"] = det["short_name"]
        if det["manager"]:
            values["director"] = docgen.fio_genitive(det["manager"])
            values["director_short"] = docgen.fio_short(det["manager"])
            sign_values["director"] = docgen.fio_short(det["manager"])
    sign_values = {k: v for k, v in sign_values.items() if k in sign_keys}

    docx_bytes = docgen.fill(tpl, values, sign_values=sign_values)
    if header and header[0]:
        _, to_line, title = header
        if det:
            frm = docgen.header_from(det["type"], det["short_name"], det["manager"], det["manager_post"])
        else:
            frm = docgen.header_from("ФЛ", fio=values.get("org_name", ""))
        docx_bytes = docgen.add_header(docx_bytes, to_line, frm, title)
    stamp_png = sign_png = None
    if det and want_stamp:
        f = orgdata.stamp_fields(det)
        w, b, u, op = QUICK_LOOKS[look]
        stamp_png = stp.to_png_bytes(stp.round_stamp(
            outer_text=f["outer"], inner_text=f["inner"], center_lines=(f["center"],),
            color=color, wear=w, breaks=b, uneven=u, opacity=op, size=600, seed=int(det["inn"][-3:])))
    if det and want_sign and det["manager"]:
        sign_png = sgn.to_png_bytes(sgn.signature_from_text(
            orgdata.signature_text(det), seed=int(det["inn"][-3:]) % 999 + 1))

    pdf_bytes = signed = None
    if docgen.soffice():
        pdf_bytes = docgen.to_pdf(docx_bytes)
        imgs = [("stamp", stamp_png), ("sign", sign_png)]
        imgs = [(k, v) for k, v in imgs if v]
        if imgs:
            needles = [sign_values.get(k) or values.get(k) for k in sign_keys] + [values.get("data")]
            spot = docgen.signature_spot(pdf_bytes, needles)
            if spot:
                pno, x0, yc, x1, pw, ph = spot
                pos = {"sign": ((x1 + 75) / pw, (yc - 6) / ph), "stamp": (0.5, (yc - 25) / ph)}
                pages = [pno]
            else:
                pos = {"sign": (0.45, 0.85), "stamp": (0.55, 0.83)}
                pages = None
            src_pdf = os.path.join(ss.tmp, "quick_src.pdf")
            with open(src_pdf, "wb") as fh:
                fh.write(pdf_bytes)
            out = stp.apply_images(src_pdf, [{"png": v, "x": pos[k][0], "y": pos[k][1], "width_mm": 40}
                                             for k, v in imgs], os.path.join(ss.tmp, "quick_signed"), pages)
            with open(out, "rb") as fh:
                signed = fh.read()
    return {"det": det, "docx": docx_bytes, "pdf": pdf_bytes, "signed": signed,
            "stamp": stamp_png, "sign": sign_png, "left": docgen.leftovers(docx_bytes)}


with tab_quick:
    st.markdown("Выберите шаблон, введите ИНН — получите готовый документ с печатью организации.")

    # --- ключ DaData (если ещё не задан)
    if not dadata_key():
        with st.container(border=True):
            st.markdown("🔑 **Нужен ключ DaData** — бесплатно на [dadata.ru](https://dadata.ru): "
                        "«Профиль» → «API-ключ». Вводится один раз за сеанс.")
            c1, c2 = st.columns([2, 1])
            c1.text_input("API-ключ DaData", key="q_key_input", type="password",
                          placeholder="Вставьте API-ключ DaData", label_visibility="collapsed")
            c2.button("Сохранить ключ", key="q_key_save", on_click=save_user_key,
                      args=("q_key_input", "q_key_msg"), use_container_width=True)
            if ss.get("q_key_msg"):
                kind_, text_ = ss.q_key_msg
                getattr(st, kind_)(text_)

    files = [f for f in docgen.BUILTIN if os.path.exists(os.path.join(docgen.TEMPLATE_DIR, f))]
    c1, c2 = st.columns([1.3, 1])
    tpl_file = c1.selectbox("Шаблон", files, format_func=lambda x: docgen.BUILTIN[x], key="q_tpl")
    with open(os.path.join(docgen.TEMPLATE_DIR, tpl_file), "rb") as fh:
        q_keys = docgen.scan(fh.read())
    needs_inn = "inn" in q_keys
    is_person = not needs_inn          # шаблон для физлица — у физлица нет печати и записи в ЕГРЮЛ
    inn = ""
    if needs_inn:
        inn = c2.text_input("ИНН организации или ИП", key="q_inn", placeholder="10 или 12 цифр")
    manual = {}
    if is_person:
        manual["org_name"] = c2.text_input("ФИО физлица", key="q_fio", placeholder="Иванов Иван Иванович")

    # --- поля, которых нет в реестре
    today = dt.date.today()
    rest = [k for k in q_keys if k not in FROM_INN]
    cols = st.columns(3)
    date_fmt = cols[0].selectbox("Формат дат", list(docgen.DATE_FORMATS), key="q_date_fmt")
    i = 1
    for k in rest:
        col = cols[i % 3]
        i += 1
        if docgen.field_type(k) == "date":
            default = today if k == "data" else today.replace(year=today.year + 1)
            manual[k] = docgen.format_date(col.date_input(docgen.label(k), default, key=f"q_{k}",
                                                          format="DD.MM.YYYY"), date_fmt)
        else:
            manual[k] = col.text_input(docgen.label(k), key=f"q_{k}", help=docgen.hint(k) or None)

    q_header = header_ui("q")

    # --- печать / подпись
    c1, c2, c3, c4 = st.columns(4)
    want_stamp = c1.checkbox("Поставить печать", True, key="q_stamp", disabled=is_person)
    want_sign = c2.checkbox("Поставить подпись", False, key="q_sign", disabled=is_person,
                            help="Подпись руководителя («И. Фамилия») рукописным шрифтом")
    look = c3.selectbox("Вид оттиска", list(QUICK_LOOKS), key="q_look", disabled=not want_stamp)
    color = QUICK_COLORS[c4.selectbox("Цвет", list(QUICK_COLORS), key="q_color", disabled=not want_stamp)]

    problems = []
    if needs_inn and not dadata_key():
        problems.append("сохраните ключ DaData")
    if needs_inn and not orgdata.inn_is_valid((inn or "").strip()):
        problems.append("введите правильный ИНН")
    if is_person and not manual.get("org_name", "").strip():
        problems.append("введите ФИО")
    empty = [docgen.label(k) for k in rest if not str(manual.get(k, "")).strip()]
    if empty:
        problems.append("заполните: " + ", ".join(empty))
    if problems:
        st.caption("Чтобы сформировать документ: " + "; ".join(problems) + ".")

    if st.button("⚡ Сформировать документ", type="primary", use_container_width=True,
                 disabled=bool(problems), key="q_go"):
        with st.spinner("Ищу организацию, заполняю шаблон, ставлю печать…"):
            try:
                ss.q_result = quick_build(tpl_file, (inn or "").strip(), manual, want_stamp and not is_person,
                                          want_sign and not is_person, look, color, q_header)
                ss.q_result["file"] = clean_name(
                    f"{os.path.splitext(tpl_file)[0]}_{(inn or manual.get('org_name', '')).strip()}_"
                    f"{today.strftime('%d.%m.%Y')}", "документ")
            except orgdata.OrgLookupError as e:
                ss.pop("q_result", None)
                st.error(str(e))
            except Exception as e:
                ss.pop("q_result", None)
                st.error(f"Ошибка: {e}")

    res = ss.get("q_result")
    if res:
        det = res["det"]
        if det:
            if det["status"] and det["status"] != "действующая":
                st.warning(f"Внимание: организация {det['status']}.")
            tpl_is_ip = tpl_file.split(".")[1] == "IP" if tpl_file.count(".") > 1 else False
            if (det["type"] == "ИП") != tpl_is_ip and needs_inn:
                st.warning("Похоже, шаблон не подходит: ИНН принадлежит "
                           + ("ИП" if det["type"] == "ИП" else "организации")
                           + ", а выбран шаблон для " + ("ИП." if tpl_is_ip else "юрлица."))
            st.success(f"**{det['short_name']}** · ИНН {det['inn']} · {det['address']}"
                       + (f"  \n{det['manager_post']}: {det['manager']}" if det["manager"] else ""))
        if res["left"]:
            st.warning("Остались незаполненные места: " + ", ".join(res["left"]))
        pc1, pc2 = st.columns([1, 1.2])
        show = res["signed"] or res["pdf"]
        if show:
            pv = pe.fitz.open(stream=show, filetype="pdf")
            pc1.image(pv[len(pv) - 1].get_pixmap(dpi=80).tobytes("png"), use_container_width=True,
                      caption="Предпросмотр")
            pv.close()
        with pc2:
            name = res["file"]
            if res["signed"]:
                st.download_button("⬇ Скачать PDF с печатью", res["signed"], name + ".pdf",
                                   "application/pdf", type="primary", use_container_width=True, key="q_dl1")
            elif res["pdf"]:
                st.download_button("⬇ Скачать PDF", res["pdf"], name + ".pdf", "application/pdf",
                                   type="primary", use_container_width=True, key="q_dl2")
            else:
                st.caption("PDF недоступен: на сервере нет LibreOffice.")
            st.download_button("⬇ Скачать DOCX (Word, без печати)", res["docx"], name + ".docx",
                               "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                               use_container_width=True, key="q_dl3")
            if res["stamp"]:
                st.download_button("⬇ Скачать печать (PNG)", res["stamp"], "печать.png", "image/png",
                                   use_container_width=True, key="q_dl4")
            if res["sign"]:
                st.download_button("⬇ Скачать подпись (PNG)", res["sign"], "подпись.png", "image/png",
                                   use_container_width=True, key="q_dl5")
