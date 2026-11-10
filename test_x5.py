
#!/usr/bin/env python3

import os
import sys
import json
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
# Cookies
# ---------------------------------------------------------

def get_cookie(cookie_file):
    """
    Читает cookies.json, экспортированный из браузера.

    Поддерживает формат:
    {
        "Request Cookies": {
            "__visitor_id": "...",
            "__keycloak_session": "...",
            "__id_token_hint": "..."
        }
    }
    """

    env_cookie = os.environ.get("X5_COOKIE")

    if env_cookie:
        return env_cookie.strip()

    path = Path(cookie_file)

    if not path.exists():
        raise FileNotFoundError(
            f"Файл cookies не найден: {path}"
        )

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    cookies = data.get("Request Cookies", data)

    if isinstance(cookies, list):
        result = []

        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value")

            if name and value is not None:
                result.append(f"{name}={value}")

        cookie_string = "; ".join(result)

    elif isinstance(cookies, dict):
        cookie_string = "; ".join(
            f"{name}={value}"
            for name, value in cookies.items()
            if value is not None
        )

    else:
        raise ValueError("Неизвестный формат cookies.json")

    if not cookie_string:
        raise ValueError("В cookies.json нет cookies")

    return cookie_string


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
        raise RuntimeError("Сервер вернул пустой ответ")

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
            "Корневой элемент Turbo Stream не является объектом"
        )

    data = decoded.get("data")

    if not isinstance(data, dict):
        raise ValueError(
            "В Turbo Stream отсутствует объект data"
        )

    # POST-ответ истории покупок.
    receipts = data.get("additionalReceiptsResponse")

    if receipts is None:
        # Поддержка старого GET-ответа.
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
            "или старый receiptsResponse"
        )

    if isinstance(receipts, dict):
        receipts = list(receipts.values())

    if not isinstance(receipts, list):
        raise ValueError(
            "Ответ со списком чеков имеет неожиданный формат"
        )

    return receipts


# ---------------------------------------------------------
# Receipt extraction
# ---------------------------------------------------------

def find_items(receipt):
    """
    Находит список товаров в чеке.
    """

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
    """
    Извлекает чеки и товары из ответа сервера.
    """

    decoded = decode_turbo_stream(raw)

    result = []

    for receipt in decoded:
        if not isinstance(receipt, dict):
            continue

        # Иногда объект чека может находиться
        # внутри дополнительной обёртки.
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
    """
    Уникальный ключ чека.
    """

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


def print_receipts(receipts):
    print()
    print("=" * 70)
    print(f"Найдено чеков: {len(receipts)}")
    print("=" * 70)

    for index, receipt in enumerate(receipts, 1):
        print()
        print(
            f"[{index}] "
            f"{receipt.get('created', 'Дата неизвестна')}"
        )

        print(
            f"Магазин: {receipt.get('nameTc') or '—'}"
        )

        print(
            f"Адрес: {receipt.get('storeAddress') or '—'}"
        )

        print(
            f"Сумма: {format_money(receipt.get('amountPromo'))}"
        )

        if receipt.get("amountRegular") is not None:
            print(
                "Без скидки: "
                f"{format_money(receipt.get('amountRegular'))}"
            )

        print(f"Номер чека: {receipt.get('checkNum') or '—'}")

        if receipt.get("fiscalNum"):
            print(f"Фискальный номер: {receipt['fiscalNum']}")

        if receipt.get("ofdurl"):
            print(f"ОФД: {receipt['ofdurl']}")

        items = receipt.get("items", [])

        print(f"Товаров: {len(items)}")

        for item in items:
            name = item.get("name") or "Без названия"
            quantity = item.get("quantity")
            price = item.get("priceItem")

            print(
                f"  - {name}: "
                f"{quantity if quantity is not None else '—'} "
                f"x {format_money(price)}"
            )


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Загрузка истории покупок X5 Club"
    )

    parser.add_argument(
        "--from",
        dest="date_from",
        help="Начальная дата YYYY-MM-DD",
    )

    parser.add_argument(
        "--to",
        dest="date_to",
        help="Конечная дата YYYY-MM-DD",
    )

    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Количество дней, если даты не указаны (по умолчанию 30)",
    )

    parser.add_argument(
        "--cookie-file",
        default="cookies.json",
        help="Файл cookies.json",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести результат в JSON",
    )

    parser.add_argument(
        "--dump",
        action="store_true",
        help="Сохранить сырые ответы сервера",
    )

    args = parser.parse_args()

    today = datetime.date.today()

    date_to = (
        datetime.date.fromisoformat(args.date_to)
        if args.date_to
        else today
    )

    date_from = (
        datetime.date.fromisoformat(args.date_from)
        if args.date_from
        else date_to - datetime.timedelta(days=args.days - 1)
    )

    if date_from > date_to:
        parser.error("Начальная дата больше конечной")

    date_from_str = date_from.isoformat()
    date_to_str = date_to.isoformat()

    print(f"Период: {date_from_str} → {date_to_str}")
    print("Подключение к X5 Club...")

    try:
        cookie = get_cookie(args.cookie_file)
    except Exception as e:
        print(f"Ошибка cookies: {e}", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers["Cookie"] = cookie

    all_receipts = []
    seen = set()

    print("Запрашиваю историю...")

    try:
        for page in range(1, 101):
            print(f"Загружаю страницу {page}...")

            raw = fetch_raw(
                session,
                date_from_str,
                date_to_str,
                page=None if page == 1 else page,
            )

            if args.dump:
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

            receipts = extract_receipts(raw)

            if not receipts:
                print("Чеков на странице нет. Завершение.")
                break

            added = 0

            for receipt in receipts:
                key = receipt_key(receipt)

                if key in seen:
                    continue

                seen.add(key)
                all_receipts.append(receipt)
                added += 1

            print(
                f"Страница {page}: "
                f"получено {len(receipts)}, "
                f"новых {added}"
            )

            if added == 0:
                print(
                    "Новых чеков нет. "
                    "Вероятно, достигнут конец истории."
                )
                break

        else:
            print(
                "Достигнут лимит в 100 страниц."
            )

    except requests.RequestException as e:
        print(f"Ошибка HTTP: {e}", file=sys.stderr)
        sys.exit(1)

    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)

    all_receipts.sort(
        key=lambda x: str(x.get("created") or ""),
        reverse=True,
    )

    if args.json:
        print(
            json.dumps(
                all_receipts,
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print_receipts(all_receipts)

    if args.dump:
        Path("receipts.json").write_text(
            json.dumps(
                all_receipts,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print("\nJSON сохранён → receipts.json")

    session.close()


if __name__ == "__main__":
    main()
