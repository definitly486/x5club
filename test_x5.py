#!/usr/bin/env python3

import os
import sys
import json
import csv
import argparse
import datetime
from pathlib import Path

import requests

BASE_URL = "https://x5club.ru/lk/history.data"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:159.0) "
    "Gecko/20100101 Firefox/159.0"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "ru",
    "Referer": "https://x5club.ru/",
    "Origin": "https://x5club.ru",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# ---------------------------------------------------------
# Таблицы
# ---------------------------------------------------------

def print_table(headers, rows):
    """Выводит таблицу без сторонних библиотек."""

    if not rows:
        print("Нет данных для отображения.")
        return

    formatted_rows = []

    for row in rows:
        formatted_rows.append([
            "—" if value is None or value == "" else str(value)
            for value in row
        ])

    widths = []

    for i, header in enumerate(headers):
        width = len(str(header))

        for row in formatted_rows:
            width = max(width, len(row[i]))

        widths.append(width)

    separator = "+-" + "-+-".join(
        "-" * width for width in widths
    ) + "-+"

    def print_row(row):
        print(
            "| " + " | ".join(
                str(value).ljust(widths[i])
                for i, value in enumerate(row)
            ) + " |"
        )

    print(separator)
    print_row(headers)
    print(separator)

    for row in formatted_rows:
        print_row(row)

    print(separator)

# ---------------------------------------------------------
# Cookies
# ---------------------------------------------------------

def get_cookie(cookie_file):
    cookie_header = os.environ.get("X5_COOKIE", "").strip()

    if cookie_header:
        print("Используются cookies из переменной X5_COOKIE.")
        return cookie_header

    path = Path(cookie_file)

    if not path.exists():
        raise ValueError(
            f"Файл {cookie_file} не найден."
        )

    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise ValueError(
            f"Не удалось прочитать {cookie_file}: {e}"
        )

    if not content:
        raise ValueError(
            f"Файл {cookie_file} не содержит куки (файл пуст)."
        )

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Файл {cookie_file} содержит некорректный JSON: {e}"
        )

    cookies = None

    if isinstance(data, dict):
        cookies = data.get("Request Cookies")

        if cookies is None:
            cookies = data.get("cookies")

        if cookies is None and data and all(
            isinstance(v, str) for v in data.values()
        ):
            cookies = data

    elif isinstance(data, list):
        cookies = data

    pairs = []
    cookie_rows = []

    if isinstance(cookies, dict):
        for name, value in cookies.items():
            valid = (
                bool(name)
                and value is not None
                and bool(str(value).strip())
            )

            cookie_rows.append([
                name,
                "Есть" if valid else "Пусто"
            ])

            if valid:
                pairs.append(f"{name}={value}")

    elif isinstance(cookies, list):
        for item in cookies:
            if not isinstance(item, dict):
                continue

            name = item.get("name")
            value = item.get("value")

            valid = (
                bool(name)
                and value is not None
                and bool(str(value).strip())
            )

            cookie_rows.append([
                name or "Без имени",
                "Есть" if valid else "Пусто"
            ])

            if valid:
                pairs.append(f"{name}={value}")

    if cookie_rows:
        print("\nCookies из файла:")

        print_table(
            ["Имя cookie", "Состояние"],
            cookie_rows
        )
    else:
        print("\nВ файле не обнаружены cookies.")

    cookie_header = "; ".join(pairs)

    if not cookie_header:
        raise ValueError(
            f"Файл {cookie_file} не содержит куки. "
            "Экспортируй cookies из браузера заново."
        )

    print(f"\nЗагружено cookies: {len(pairs)}")

    return cookie_header

# ---------------------------------------------------------
# HTTP
# ---------------------------------------------------------

def fetch_raw(session, date_from, date_to, page=None):
    """
    Получает историю покупок.

    Первая страница:
        from=...
        to=...
        type=receipts
        codeTc=D,S,VK,SL,VP
        isNewData=true

    Следующие страницы:
        page=2, page=3...
    """

    data = {
        "from": date_from,
        "to": date_to,
        "type": "receipts",
        "codeTc": "D,S,VK,SL,VP",
    }

    if page is None:
        data["isNewData"] = "true"
    else:
        data["page"] = str(page)

    response = session.post(
        BASE_URL,
        data=data,
        headers={
            "Content-Type":
                "application/x-www-form-urlencoded;charset=UTF-8"
        },
        timeout=40,
    )

    if response.status_code in (401, 403):
        raise RuntimeError(
            f"Ошибка авторизации: HTTP {response.status_code}. "
            "Обнови cookies.json."
        )

    if response.url and "login" in response.url.lower():
        raise RuntimeError(
            "Сайт перенаправил на страницу авторизации. "
            "Обнови cookies.json."
        )

    response.raise_for_status()

    raw = response.text.strip()

    if not raw:
        raise RuntimeError("Сервер вернул пустой ответ.")

    if raw.startswith("<"):
        raise RuntimeError(
            "Вместо данных получен HTML. "
            "Возможно, сессия истекла."
        )

    return raw

# ---------------------------------------------------------
# Turbo Stream decoder
# ---------------------------------------------------------

def decode_turbo_stream(raw):
    """
    Декодирует Remix Turbo Stream.

    В ответе используется таблица ссылок:
        {"_1": 2}
        "data"
        {"_3": 4, ...}

    Ключ _N ссылается на строку с индексом N,
    значение является ссылкой на другой элемент таблицы.
    """

    try:
        table = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Не удалось разобрать JSON: {e}"
        )

    if not isinstance(table, list):
        return table

    cache = {}
    resolving = set()

    def resolve(ref):
        if not isinstance(ref, int):
            return ref

        if ref < 0 or ref >= len(table):
            return ref

        if ref in cache:
            return cache[ref]

        if ref in resolving:
            return None

        resolving.add(ref)

        value = table[ref]

        if isinstance(value, list):
            result = [resolve(x) for x in value]

        elif isinstance(value, dict):
            result = {}

            for key, child_ref in value.items():
                if key.startswith("_") and key[1:].isdigit():
                    key_index = int(key[1:])

                    if key_index < len(table):
                        property_name = table[key_index]
                    else:
                        property_name = key

                    if isinstance(property_name, str):
                        result[property_name] = resolve(child_ref)
                else:
                    result[key] = resolve(child_ref)

        elif isinstance(value, str):
            result = value

        else:
            result = value

        resolving.remove(ref)
        cache[ref] = result

        return result

    decoded = resolve(0)

    if not isinstance(decoded, dict):
        raise ValueError(
            "Корневой элемент Turbo Stream не является объектом."
        )

    data = decoded.get("data")

    if not isinstance(data, dict):
        raise ValueError(
            "В Turbo Stream отсутствует объект data."
        )

    receipts = data.get("additionalReceiptsResponse")

    if receipts is None:
        routes = decoded.get("routes")

        if isinstance(routes, dict):
            auth = routes.get("_auth", {})
            lk = auth.get("lk", {})
            history = lk.get("history", {})
            history_data = history.get("data", {})

            receipts_response = history_data.get(
                "receiptsResponse", {}
            )

            if isinstance(receipts_response, dict):
                receipts = receipts_response.get("data")

    if receipts is None:
        raise ValueError(
            "Не найден additionalReceiptsResponse "
            "или старый receiptsResponse."
        )

    if isinstance(receipts, dict):
        receipts = list(receipts.values())

    if not isinstance(receipts, list):
        raise ValueError(
            "Ответ со списком чеков имеет неожиданный формат."
        )

    return receipts

# ---------------------------------------------------------
# Receipt extraction
# ---------------------------------------------------------

def find_items(receipt):
    """Находит список товаров в чеке."""

    for key in (
        "items",
        "positions",
        "receiptItems",
        "products",
        "goods",
    ):
        value = receipt.get(key)

        if isinstance(value, list):
            return value

    return []

def extract_receipts(raw):
    """Извлекает чеки и товары из ответа сервера."""

    decoded = decode_turbo_stream(raw)

    result = []

    for receipt in decoded:
        if not isinstance(receipt, dict):
            continue

        if "receipt" in receipt and isinstance(
            receipt["receipt"], dict
        ):
            receipt = receipt["receipt"]

        if not any(
            key in receipt
            for key in (
                "rtlTxnId",
                "checkNum",
                "fiscalNum",
                "created",
            )
        ):
            continue

        items = find_items(receipt)

        normalized_items = []

        for item in items:
            if not isinstance(item, dict):
                continue

            normalized_items.append({
                "pluId": item.get("pluId"),
                "name": item.get("name"),
                "quantity": item.get("quantity"),
                "weightUomCode": item.get("weightUomCode"),
                "priceRegular": item.get("priceRegular"),
                "pricePromo": item.get("pricePromo"),
                "priceItem": item.get("priceItem"),
            })

        normalized = {
            "rtlTxnId": receipt.get("rtlTxnId"),
            "created": receipt.get("created"),
            "title": receipt.get("title"),
            "nameTc": receipt.get("nameTc"),
            "amountPromo": receipt.get("amountPromo"),
            "amountRegular": receipt.get("amountRegular"),
            "checkNum": receipt.get("checkNum"),
            "fiscalNum": receipt.get("fiscalNum"),
            "fiscalSignNum": receipt.get("fiscalSignNum"),
            "storeAddress": receipt.get("storeAddress"),
            "ofdurl": receipt.get("ofdurl"),
            "items": normalized_items,
        }

        result.append(normalized)

    return result

# ---------------------------------------------------------
# Deduplication
# ---------------------------------------------------------

def receipt_key(receipt):
    """Уникальный ключ чека."""

    return (
        receipt.get("rtlTxnId"),
        receipt.get("checkNum"),
        receipt.get("created"),
    )

# ---------------------------------------------------------
# Formatting
# ---------------------------------------------------------

def format_money(value):
    if value is None:
        return "—"

    try:
        return f"{float(value):.2f} ₽"
    except (ValueError, TypeError):
        return str(value)

def get_shop_name(receipt):
    return receipt.get("nameTc") or receipt.get("title") or "—"

# ---------------------------------------------------------
# Receipt table
# ---------------------------------------------------------

def print_receipts(receipts):
    print()
    print("=" * 70)
    print(f"НАЙДЕНО ЧЕКОВ: {len(receipts)}")
    print("=" * 70)

    rows = []

    for index, receipt in enumerate(receipts, 1):
        rows.append([
            index,
            receipt.get("created"),
            get_shop_name(receipt),
            format_money(receipt.get("amountPromo")),
            receipt.get("checkNum"),
            len(receipt.get("items", [])),
        ])

    print_table(
        [
            "№",
            "Дата",
            "Магазин",
            "Сумма",
            "№ чека",
            "Товаров",
        ],
        rows,
    )

    total = 0.0

    for receipt in receipts:
        try:
            total += float(receipt.get("amountPromo") or 0)
        except (ValueError, TypeError):
            pass

    print(f"\nОбщая сумма покупок: {format_money(total)}")

# ---------------------------------------------------------
# Products table
# ---------------------------------------------------------

def print_items(receipts):
    rows = []
    number = 0

    for receipt in receipts:
        for item in receipt.get("items", []):
            number += 1

            rows.append([
                number,
                receipt.get("created"),
                get_shop_name(receipt),
                receipt.get("checkNum"),
                item.get("name"),
                item.get("quantity"),
                item.get("weightUomCode"),
                format_money(item.get("priceRegular")),
                format_money(item.get("pricePromo")),
                format_money(item.get("priceItem")),
            ])

    print("\n" + "=" * 70)
    print(f"ТОВАРЫ В ЧЕКАХ: {number}")
    print("=" * 70)

    print_table(
        [
            "№",
            "Дата",
            "Магазин",
            "Чек",
            "Товар",
            "Кол-во",
            "Ед.",
            "Обычная цена",
            "Цена со скидкой",
            "Сумма",
        ],
        rows,
    )

# ---------------------------------------------------------
# CSV export
# ---------------------------------------------------------

def export_csv(receipts, filename):
    """
    Экспортирует покупки в CSV.

    Одна строка CSV = один товар.
    Информация о чеке повторяется для каждого товара.
    """

    columns = [
        "Дата",
        "Магазин",
        "Номер чека",
        "Фискальный номер",
        "Фискальный признак",
        "Адрес магазина",
        "Сумма чека",
        "Сумма без скидки",
        "ОФД",
        "PLU",
        "Название товара",
        "Количество",
        "Единица измерения",
        "Обычная цена",
        "Цена со скидкой",
        "Сумма товара",
    ]

    count = 0

    try:
        with open(
            filename,
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=columns,
                delimiter=";",
                extrasaction="ignore",
            )

            writer.writeheader()

            for receipt in receipts:
                items = receipt.get("items") or []

                for item in items:
                    if not isinstance(item, dict):
                        continue

                    writer.writerow({
                        "Дата": receipt.get("created", ""),
                        "Магазин": get_shop_name(receipt),
                        "Номер чека": receipt.get("checkNum", ""),
                        "Фискальный номер": receipt.get("fiscalNum", ""),
                        "Фискальный признак": receipt.get("fiscalSignNum", ""),
                        "Адрес магазина": receipt.get("storeAddress", ""),
                        "Сумма чека": receipt.get("amountPromo", ""),
                        "Сумма без скидки": receipt.get("amountRegular", ""),
                        "ОФД": receipt.get("ofdurl", ""),
                        "PLU": item.get("pluId", ""),
                        "Название товара": item.get("name", ""),
                        "Количество": item.get("quantity", ""),
                        "Единица измерения": item.get("weightUomCode", ""),
                        "Обычная цена": item.get("priceRegular", ""),
                        "Цена со скидкой": item.get("pricePromo", ""),
                        "Сумма товара": item.get("priceItem", ""),
                    })

                    count += 1

    except OSError as e:
        raise RuntimeError(
            f"Не удалось сохранить CSV {filename}: {e}"
        )

    print(f"\nCSV сохранён → {filename}")
    print(f"Экспортировано товаров: {count}")
    print(f"Чеков в экспорте: {len(receipts)}")

# ---------------------------------------------------------
# JSON export
# ---------------------------------------------------------

def save_json(receipts, filename="receipts.json"):
    try:
        Path(filename).write_text(
            json.dumps(
                receipts,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as e:
        raise RuntimeError(
            f"Не удалось сохранить JSON: {e}"
        )

    print(f"\nJSON сохранён → {filename}")

# ---------------------------------------------------------
# Raw response dump
# ---------------------------------------------------------

def save_raw(raw, page):
    filename = (
        "response.raw.txt"
        if page == 1
        else f"response.page{page}.raw.txt"
    )

    Path(filename).write_text(
        raw,
        encoding="utf-8",
    )

    print(f"Сырой ответ → {filename}")

# ---------------------------------------------------------
# Date parsing (ключ сортировки)
# ---------------------------------------------------------

def created_timestamp(receipt):
    """
    Дата чека в epoch seconds для корректной сортировки.

    created приходит строкой (например "12.11.2026 18:24"),
    строковая сортировка ломает порядок на диапазоне
    через несколько месяцев. Нераспознанные даты получают
    -inf и уходят в конец списка (сортировка стабильная).
    """

    s = str(receipt.get("created") or "").strip()

    if not s:
        return float("-inf")

    # Unix-время: секунды (10 цифр) или миллисекунды (13 цифр)
    if s.isdigit():
        if len(s) == 13:
            return int(s) / 1000.0
        if len(s) == 10:
            return float(s)
        return float("-inf")

    # ISO: 2026-11-12T18:24:00, 202
