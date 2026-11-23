"""
Реквизиты организации / ИП по ИНН через официальный API DaData.

Ключ берётся из переменной окружения DADATA_API_KEY
(бесплатный ключ: зарегистрируйтесь на dadata.ru → «Профиль» → «API-ключ»).
Ключ нельзя хранить в коде и в репозитории.
"""
import json
import os
import re
import urllib.error
import urllib.request

API_URL = os.environ.get(
    "DADATA_API_URL", "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party")


class OrgLookupError(Exception):
    pass


# Встроенный ключ (по просьбе владельца). Переменная окружения DADATA_API_KEY имеет приоритет.
# ВНИМАНИЕ: репозиторий публичный — при утечке выпустите новый ключ в личном кабинете DaData.
BUILTIN_KEY = "5c4af1b9ea282edee0e5bdd730a0d220632d5af4"


def api_key():
    return os.environ.get("DADATA_API_KEY", "").strip() or BUILTIN_KEY


def inn_is_valid(inn):
    """Проверка контрольных цифр ИНН (10 цифр — организация, 12 — ИП/физлицо)."""
    if not re.fullmatch(r"\d{10}|\d{12}", inn or ""):
        return False
    d = [int(c) for c in inn]

    def check(coefs):
        return sum(c * x for c, x in zip(coefs, d)) % 11 % 10

    if len(d) == 10:
        return check([2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[9]
    return (check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[10]
            and check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[11])


def find_by_inn(inn, key=None, timeout=10):
    """Возвращает первую найденную запись DaData (словарь) или None."""
    inn = re.sub(r"\D", "", inn or "")
    if not inn_is_valid(inn):
        raise OrgLookupError("Неверный ИНН: проверьте цифры (10 для организации, 12 для ИП).")
    key = key or api_key()
    if not key:
        raise OrgLookupError("Не задан ключ DaData: добавьте переменную окружения DADATA_API_KEY.")
    req = urllib.request.Request(
        API_URL, data=json.dumps({"query": inn, "count": 1}).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": "Token " + key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise OrgLookupError("DaData отклонила ключ (неверный или неактивный DADATA_API_KEY).")
        if e.code == 429:
            raise OrgLookupError("Превышен лимит запросов DaData, попробуйте позже.")
        raise OrgLookupError(f"Ошибка DaData: HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise OrgLookupError(f"Нет связи с DaData: {getattr(e, 'reason', e)}")
    items = payload.get("suggestions") or []
    return items[0] if items else None


def _quotes(s):
    """"Ромашка" → «Ромашка»."""
    s = s or ""
    out, open_ = [], True
    for ch in s:
        if ch == '"':
            out.append("«" if open_ else "»")
            open_ = not open_
        else:
            out.append(ch)
    return "".join(out)


STATUS = {"ACTIVE": "действующая", "LIQUIDATING": "ликвидируется", "LIQUIDATED": "ликвидирована",
          "BANKRUPT": "банкротство", "REORGANIZING": "реорганизация"}


def to_details(item):
    """Приводит ответ DaData к простому виду."""
    d = item.get("data") or {}
    name = d.get("name") or {}
    addr = (d.get("address") or {})
    adata = addr.get("data") or {}
    city = adata.get("city") or adata.get("settlement") or adata.get("region") or ""
    city_type = "г." if adata.get("city") else ""
    mgmt = d.get("management") or {}
    fio = d.get("fio") or {}
    is_ip = d.get("type") == "INDIVIDUAL"
    if is_ip and not fio:
        parts = (name.get("full") or "").split()
        fio = dict(zip(("surname", "name", "patronymic"), parts))
    return {
        "type": "ИП" if is_ip else "ЮЛ",
        "full_name": _quotes(name.get("full_with_opf") or item.get("unrestricted_value") or ""),
        "short_name": _quotes(name.get("short_with_opf") or item.get("value") or ""),
        "inn": d.get("inn") or "",
        "ogrn": d.get("ogrn") or "",
        "kpp": d.get("kpp") or "",
        "city": f"{city_type} {city}".strip(),
        "address": addr.get("value") or "",
        "manager": mgmt.get("name") or " ".join(v for v in (fio.get("surname"), fio.get("name"),
                                                             fio.get("patronymic")) if v),
        "manager_post": mgmt.get("post") or ("Индивидуальный предприниматель" if is_ip else ""),
        "fio": fio,
        "status": STATUS.get((d.get("state") or {}).get("status"), ""),
    }


def stamp_fields(det):
    """Поля круглой печати по реквизитам."""
    if det["type"] == "ИП":
        f = det["fio"]
        outer = "ИНДИВИДУАЛЬНЫЙ ПРЕДПРИНИМАТЕЛЬ " + " ".join(
            v for v in (f.get("surname"), f.get("name"), f.get("patronymic")) if v)
        inner = f"ИНН {det['inn']} • ОГРНИП {det['ogrn']}"
    else:
        outer = det["full_name"]
        inner = f"ИНН {det['inn']} • ОГРН {det['ogrn']}"
    return {"outer": outer.upper(), "inner": inner, "center": det["city"]}


def signature_text(det):
    """«И. Фамилия» руководителя (или ИП) для подписи."""
    parts = (det.get("manager") or "").split()
    if not parts:
        return ""
    return f"{parts[1][0]}. {parts[0]}" if len(parts) > 1 else parts[0]
