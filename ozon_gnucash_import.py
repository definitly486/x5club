#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Импорт чеков X5 Club в GnuCash.
Работает с обычным и gzip-сжатым XML-файлом GnuCash.
Требуется только Python 3 + Tkinter (стандартная библиотека).
"""
import gzip
import json
import os
import re
import shutil
import tempfile
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

NS = {
    "gnc": "http://www.gnucash.org/XML/gnc",
    "act": "http://www.gnucash.org/XML/act",
    "trn": "http://www.gnucash.org/XML/trn",
    "split": "http://www.gnucash.org/XML/split",
    "cmdty": "http://www.gnucash.org/XML/cmdty",
    "ts": "http://www.gnucash.org/XML/ts",
}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)
ET.register_namespace("book", "http://www.gnucash.org/XML/book")
ET.register_namespace("slot", "http://www.gnucash.org/XML/slot")
ET.register_namespace("g", "http://www.gnucash.org/XML/g")

def q(prefix, tag):
    return f"{{{NS[prefix]}}}{tag}"

def guid():
    return uuid.uuid4().hex

def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def cents(value):
    return int((money(value) * 100).to_integral_value())

def parse_fraction(value):
    a, b = value.split("/", 1)
    return Decimal(a) / Decimal(b)

def norm(text):
    text = (text or "").lower().replace("ё", "е")
    return re.sub(r"[^0-9a-zа-я]+", " ", text).strip()

def read_gnucash(path):
    raw = Path(path).read_bytes()
    compressed = raw[:2] == b"\x1f\x8b"
    xml = gzip.decompress(raw) if compressed else raw
    return ET.fromstring(xml), compressed

def load_receipts(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    # В файле X5 перед JSON находится текстовый заголовок, а после — итог.
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("В файле не найден JSON-массив чеков.")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("Ожидался список чеков JSON.")
    return data

def account_data(root):
    accounts = {}
    for node in root.findall(".//gnc:account", NS):
        aid = node.findtext("act:id", default="", namespaces=NS)
        name = node.findtext("act:name", default="", namespaces=NS)
        typ = node.findtext("act:type", default="", namespaces=NS)
        parent = node.findtext("act:parent", default="", namespaces=NS)
        if aid:
            accounts[aid] = {"id": aid, "name": name, "type": typ, "parent": parent}
    def path(aid):
        parts, seen = [], set()
        while aid in accounts and aid not in seen:
            seen.add(aid)
            item = accounts[aid]
            parts.append(item["name"])
            aid = item["parent"]
        return ":".join(reversed(parts))
    for aid, item in accounts.items():
        item["path"] = path(aid)
    return accounts

def find_account_by_path(accounts, expected):
    target = norm(expected).replace(" ", "")
    found = [a for a in accounts.values()
             if norm(a["path"]).replace(" ", "") == target]
    if len(found) != 1:
        raise ValueError(f"Не удалось однозначно найти счёт: {expected}. Найдено: {len(found)}")
    return found[0]

def find_pending_transactions(root, discrepancy_id, bank_id):
    """Ищет уже существующие банковские транзакции: +Дисбаланс / -Ozon Банк."""
    result = []
    for txn in root.findall(".//gnc:transaction", NS):
        splits = txn.findall("trn:splits/trn:split", NS)
        imbalance = []
        bank = []
        for sp in splits:
            aid = sp.findtext("split:account", default="", namespaces=NS)
            value = parse_fraction(sp.findtext("split:value", default="0/1", namespaces=NS))
            if aid == discrepancy_id and value > 0:
                imbalance.append((sp, value))
            if aid == bank_id and value < 0:
                bank.append((sp, value))
        for imbalance_split, amount in imbalance:
            if any(abs(bank_value) == amount for _, bank_value in bank):
                result.append({
                    "txn": txn,
                    "split": imbalance_split,
                    "amount_cents": int((amount * 100).to_integral_value()),
                    "date": txn.findtext("trn:date-posted/ts:date", default="", namespaces=NS)[:10],
                    "description": txn.findtext("trn:description", default="", namespaces=NS),
                })
    return result

def product_accounts(accounts, parent_id):
    """Возвращает все уровни субсчетов внутри Продукты, не только первый."""
    result = []
    frontier = [parent_id]
    while frontier:
        current = frontier.pop(0)
        descendants = [a for a in accounts.values() if a["parent"] == current]
        result.extend(descendants)
        frontier.extend(a["id"] for a in descendants)
    return result

def find_product_parent(accounts):
    # Ищем именно Расходы:Пропитание:Продукты, а не одноимённый счёт в другом месте.
    matches = [
        a for a in accounts.values()
        if norm(a["name"]) == "продукты"
        and "расходы:пропитание:продукты" in norm(a["path"]).replace(" ", "")
    ]
    if not matches:
        matches = [
            a for a in accounts.values()
            if norm(a["name"]) == "продукты" and "пропитание" in norm(a["path"])
        ]
    if len(matches) != 1:
        raise ValueError("Не удалось однозначно найти счёт Расходы → Пропитание → Продукты.")
    return matches[0]

ALIASES = {
    "мини сушки": "сушки",
    "сушки": "сушки",
    "макаронные": "макароны",
    "спагетти": "макароны",
    "вермишель": "макароны",
    "бульон": "бульон говяж",
    "томаты": "помидоры",
    "помидоры": "помидоры",
    "огурцы": "огурцы",
    "мармелад": "мармелад",
    "говядина русь тушеная": "тушенка",
    "чипсы картофельные": "чипсы",
    "Чай черный Принцесса Нури Высокогорный": "чай нури",
}

def build_aliases(children):
    """Объединяет ручные синонимы со всеми субсчетами из Продукты.

    Каждый дочерний счёт автоматически становится собственным алиасом.
    Поэтому при добавлении новых категорий в GnuCash код менять не нужно.
    """
    aliases = dict(ALIASES)
    for account in children:
        account_name = norm(account["name"])
        if account_name:
            aliases.setdefault(account_name, account["name"])
    return aliases

def map_item(item, children):
    item_name = norm(item.get("name", ""))
    aliases = build_aliases(children)

    # Сначала специальные синонимы, затем названия ВСЕХ счетов из дерева
    # Расходы → Пропитание → Продукты. Более длинные фразы приоритетнее.
    for phrase, target in sorted(aliases.items(), key=lambda x: len(norm(x[0])), reverse=True):
        phrase_norm = norm(phrase)
        if phrase_norm and phrase_norm in item_name:
            matches = [a for a in children if norm(a["name"]) == norm(target)]
            if len(matches) == 1:
                return matches[0]

    # Дополнительное прямое сопоставление по полному названию счёта.
    matches = []
    for account in children:
        key = norm(account["name"])
        if key and (key in item_name or item_name in key):
            matches.append(account)
    matches.sort(key=lambda a: len(norm(a["name"])), reverse=True)
    if matches:
        if len(matches) == 1 or len(norm(matches[0]["name"])) > len(norm(matches[1]["name"])):
            return matches[0]
    return None

def transaction_check_ids(root):
    found = set()
    for node in root.findall(".//gnc:transaction", NS):
        desc = node.findtext("trn:description", default="", namespaces=NS)
        m = re.search(r"\bчек\s*№\s*(\d+)\b", desc, flags=re.I)
        if m:
            found.add(m.group(1))
    return found

def make_text(parent, prefix, tag, value, attrs=None):
    node = ET.SubElement(parent, q(prefix, tag), attrs or {})
    node.text = str(value)
    return node

def add_split(parent, account_id, amount_cents, memo=""):
    split = ET.SubElement(parent, q("trn", "split"))
    make_text(split, "split", "id", guid(), {"type": "guid"})
    make_text(split, "split", "reconciled-state", "n")
    make_text(split, "split", "value", f"{amount_cents}/100")
    make_text(split, "split", "quantity", f"{amount_cents}/100")
    if memo:
        make_text(split, "split", "memo", memo)
    make_text(split, "split", "account", account_id, {"type": "guid"})

def fill_existing_transaction(receipt, existing, mapped):
    """Заполняет существующую банковскую проводку вместо создания новой."""
    txn = existing["txn"]
    imbalance_split = existing["split"]
    splits_node = txn.find("trn:splits", NS)
    if splits_node is None:
        raise ValueError("У найденной банковской проводки отсутствует список splits.")

    # Удаляем только положительный split дисбаланса. Отрицательный split
    # Ozon Банка и GUID/дата/прочие реквизиты исходной транзакции сохраняются.
    splits_node.remove(imbalance_split)

    for item, account in mapped:
        amount = cents(item.get("pricePromo", item.get("priceRegular", "0")))
        add_split(splits_node, account["id"], amount, item.get("name", ""))

    desc = f"Пятёрочка — чек №{receipt.get('checkNum')} (X5 Club)"
    desc_node = txn.find("trn:description", NS)
    if desc_node is None:
        make_text(txn, "trn", "description", desc)
    else:
        desc_node.text = desc

    # Проверяем, что новая транзакция сбалансирована до записи.
    total = Decimal("0")
    for sp in splits_node.findall("trn:split", NS):
        total += parse_fraction(sp.findtext("split:value", default="0/1", namespaces=NS))
    if total != 0:
        raise ValueError(f"После заполнения проводка не сбалансирована: {total} RUB")
    return txn


def receipt_date(value):
    """Возвращает дату чека YYYY-MM-DD без сдвига календарного дня."""
    if value is None:
        raise ValueError("У чека отсутствует дата created.")
    text = str(value).strip()
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"Не удалось распознать дату чека: {text}")


def receipt_payment_method(receipt):
    """Возвращает только явно указанную маску способа оплаты."""
    value = receipt.get("paymentMethod")
    if isinstance(value, dict):
        value = value.get("maskedPan") or value.get("cardNumber") or value.get("value")
    return str(value or "").strip()


def create_receipt_transaction(root, receipt, bank, mapped):
    """Создаёт завершённую RUB-транзакцию: расходы по товарам и списание с Ozon."""
    amount_total = cents(receipt.get("amountPromo", 0))
    items_total = sum(
        cents(item.get("pricePromo", item.get("priceRegular", "0")))
        for item, _account in mapped
    )
    if amount_total <= 0 or items_total != amount_total:
        raise ValueError(
            f"Чек №{receipt.get('checkNum')}: сумма товаров "
            f"{Decimal(items_total) / 100:.2f} не равна сумме чека "
            f"{Decimal(amount_total) / 100:.2f} RUB."
        )

    book = root.find("gnc:book", NS)
    if book is None:
        raise ValueError("В файле GnuCash не найден элемент book.")

    txn = ET.SubElement(book, q("gnc", "transaction"), {"version": "2.0.0"})
    make_text(txn, "trn", "id", guid(), {"type": "guid"})
    currency = ET.SubElement(txn, q("trn", "currency"))
    make_text(currency, "cmdty", "space", "CURRENCY")
    make_text(currency, "cmdty", "id", "RUB")

    posted = ET.SubElement(txn, q("trn", "date-posted"))
    make_text(posted, "ts", "date", f"{receipt_date(receipt.get('created'))} 12:00:00 +0000")
    entered = ET.SubElement(txn, q("trn", "date-entered"))
    make_text(entered, "ts", "date", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000"))

    check_num = receipt.get("checkNum")
    description = f"Пятёрочка — чек №{check_num} (X5 Club)"
    make_text(txn, "trn", "description", description)
    splits = ET.SubElement(txn, q("trn", "splits"))

    for item, account in mapped:
        value = cents(item.get("pricePromo", item.get("priceRegular", "0")))
        add_split(splits, account["id"], value, item.get("name", ""))

    add_split(splits, bank["id"], -amount_total)

    total = sum(
        (parse_fraction(sp.findtext("split:value", default="0/1", namespaces=NS))
         for sp in splits.findall("trn:split", NS)),
        Decimal("0"),
    )
    if total != 0:
        raise ValueError(f"Новая транзакция не сбалансирована: {total} RUB.")
    return txn

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Импорт чеков X5 в GnuCash")
        self.root.geometry("1050x700")
        self.gnucash_path = tk.StringVar()
        self.json_path = tk.StringVar()
        self.status = tk.StringVar(value="Выберите файл GnuCash и файл с чеками.")
        self.analysis = None
        self.ozon_enabled = tk.BooleanVar(value=True)
        self.psb_enabled = tk.BooleanVar(value=False)
        self.sber_enabled = tk.BooleanVar(value=False)
        self.build_ui()

    def build_ui(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Файл GnuCash:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(outer, textvariable=self.gnucash_path).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(outer, text="Обзор…", command=self.pick_gnucash).grid(row=0, column=2)
        ttk.Label(outer, text="Файл чеков JSON:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(outer, textvariable=self.json_path).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(outer, text="Обзор…", command=self.pick_json).grid(row=1, column=2)
        cards = ttk.LabelFrame(outer, text="Способы оплаты", padding=6)
        cards.grid(row=2, column=0, columnspan=3, sticky="ew", pady=6)
        ttk.Checkbutton(cards, text="Карта Озон", variable=self.ozon_enabled).pack(side="left", padx=8)
        ttk.Checkbutton(cards, text="Карта ПСБ", variable=self.psb_enabled, state="disabled").pack(side="left", padx=8)
        ttk.Checkbutton(cards, text="Карта Сбербанка", variable=self.sber_enabled, state="disabled").pack(side="left", padx=8)
        ttk.Button(outer, text="Проверить и найти чеки", command=self.analyze).grid(row=3, column=0, columnspan=3, sticky="ew", pady=8)
        self.info = tk.Text(outer, height=8, wrap="word")
        self.info.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=5)
        cols = ("item", "account", "amount")
        self.table = ttk.Treeview(outer, columns=cols, show="headings", height=14)
        self.table.heading("item", text="Товар из чека")
        self.table.heading("account", text="Счёт GnuCash")
        self.table.heading("amount", text="Сумма, RUB")
        self.table.column("item", width=470)
        self.table.column("account", width=300)
        self.table.column("amount", width=120, anchor="e")
        self.table.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=5)
        ttk.Label(outer, textvariable=self.status).grid(row=6, column=0, columnspan=3, sticky="w", pady=6)
        self.save_btn = ttk.Button(outer, text="Создать проводки и сохранить GnuCash", command=self.save, state="disabled")
        self.save_btn.grid(row=7, column=0, columnspan=3, sticky="ew", pady=8)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(4, weight=0)
        outer.rowconfigure(5, weight=1)

    def pick_gnucash(self):
        p = filedialog.askopenfilename(title="Выберите файл GnuCash", filetypes=[("GnuCash", "*.gnucash *.gnc"), ("Все файлы", "*")])
        if p:
            self.gnucash_path.set(p)

    def pick_json(self):
        p = filedialog.askopenfilename(title="Выберите файл с чеками", filetypes=[("JSON / текст", "*.json *.txt"), ("Все файлы", "*")])
        if p:
            self.json_path.set(p)

    def log(self, text):
        self.info.insert("end", text + "\n")
        self.info.see("end")

    def analyze(self):
        self.info.delete("1.0", "end")
        for row in self.table.get_children():
            self.table.delete(row)
        self.save_btn.config(state="disabled")
        self.analysis = None
        try:
            if not self.ozon_enabled.get():
                raise ValueError("Для импорта чеков необходимо включить «Карта Озон».")
            root, compressed = read_gnucash(self.gnucash_path.get())
            receipts = load_receipts(self.json_path.get())
            accounts = account_data(root)
            bank = find_account_by_path(
                accounts, "Root Account:Активы:Текущие активы:ozon банк"
            )
            parent = find_product_parent(accounts)
            children = product_accounts(accounts, parent["id"])
            self.log(f"Банковский счёт: {bank['path']}")
            self.log(f"Счёт товаров: {parent['path']}")
            self.log(f"Категорий для сопоставления: {len(children)}")

            selected = [r for r in receipts
                        if receipt_payment_method(r) == "************7961"]
            without_method = sum(1 for r in receipts if not receipt_payment_method(r))
            self.log(f"Всего чеков в JSON: {len(receipts)}")
            self.log(f"Чеков с картой Озон ************7961: {len(selected)}")
            if without_method:
                self.log(f"Чеков без paymentMethod: {without_method} (они пропущены)")
            if not selected:
                self.log("Подходящих чеков нет. Проверьте, что JSON содержит paymentMethod.")
                self.status.set("Нет чеков с точной маской карты Ozon. GnuCash не изменён.")
                return

            known_ids = transaction_check_ids(root)
            existing_bank = {}
            for txn in root.findall(".//gnc:transaction", NS):
                date_text = txn.findtext("trn:date-posted/ts:date", default="", namespaces=NS)[:10]
                for split in txn.findall("trn:splits/trn:split", NS):
                    if split.findtext("split:account", default="", namespaces=NS) != bank["id"]:
                        continue
                    value = parse_fraction(split.findtext("split:value", default="0/1", namespaces=NS))
                    if value < 0:
                        key = (date_text, abs(int((value * 100).to_integral_value())))
                        existing_bank.setdefault(key, []).append(txn)

            pending = find_pending_transactions(
                root,
                find_account_by_path(accounts, "Root Account:Дисбаланс-RUB")["id"],
                bank["id"],
            )
            plans = []
            skipped = 0
            for receipt in selected:
                check_num = str(receipt.get("checkNum", "")).strip()
                if not check_num:
                    self.log("Пропущен чек без checkNum.")
                    skipped += 1
                    continue
                if check_num in known_ids:
                    skipped += 1
                    continue

                amount = cents(receipt.get("amountPromo", 0))
                day = receipt_date(receipt.get("created"))
                if amount <= 0:
                    self.log(f"Чек №{check_num}: некорректная сумма.")
                    skipped += 1
                    continue

                # Если в GnuCash уже есть банковское списание на ту же дату и сумму,
                # не создаём дубль. Незаполненную проводку разрешаем заполнить.
                same_day = [e for e in pending
                            if e["amount_cents"] == amount and e["date"] == day]
                if len(same_day) > 1:
                    self.log(f"Чек №{check_num}: несколько незаполненных проводок на эту дату/сумму — пропуск.")
                    skipped += 1
                    continue
                if same_day:
                    action = "fill"
                    entry = same_day[0]
                elif existing_bank.get((day, amount)):
                    self.log(f"Чек №{check_num}: списание {day} на {money(amount / 100):.2f} RUB уже есть — пропуск.")
                    skipped += 1
                    continue
                else:
                    action = "create"
                    entry = None

                mapped, missing, items_total = [], [], 0
                for item in receipt.get("items", []):
                    item_amount = cents(item.get("pricePromo", item.get("priceRegular", "0")))
                    items_total += item_amount
                    account = map_item(item, children)
                    if account is None:
                        missing.append(item.get("name", "(без названия)"))
                    else:
                        mapped.append((item, account))
                        self.table.insert("", "end", values=(
                            f"Чек №{check_num}: {item.get('name', '')}",
                            account["name"],
                            f"{Decimal(item_amount) / 100:.2f}",
                        ))
                if missing:
                    self.log(f"Чек №{check_num}: не сопоставлены товары: {', '.join(missing)}")
                    skipped += 1
                    continue
                if items_total != amount:
                    self.log(
                        f"Чек №{check_num}: сумма товаров {Decimal(items_total)/100:.2f} "
                        f"не равна сумме чека {Decimal(amount)/100:.2f} RUB."
                    )
                    skipped += 1
                    continue
                plans.append({"receipt": receipt, "mapped": mapped, "action": action, "entry": entry})

            self.log(f"Готово к импорту: {len(plans)}; пропущено/уже учтено: {skipped}")
            self.log("Новые операции будут созданы как сбалансированные RUB-проводки: товары в расходы, списание с Ozon.")
            if not plans:
                self.status.set("Новых чеков для импорта нет. GnuCash не изменён.")
                return
            self.analysis = (root, compressed, plans)
            self.status.set(f"Проверка завершена: подготовлено чеков к импорту — {len(plans)}.")
            self.save_btn.config(state="normal")
        except Exception as exc:
            messagebox.showerror("Ошибка проверки", str(exc))
            self.status.set("Ошибка. Файл не изменён.")

    def save(self):
        if not self.analysis:
            return
        root, compressed, plans = self.analysis
        count_new = sum(1 for p in plans if p["action"] == "create")
        count_fill = len(plans) - count_new
        prompt = (
            f"Импортировать чеков: {len(plans)}\\n"
            f"Новых операций: {count_new}\\n"
            f"Заполнить существующих незавершённых: {count_fill}\\n\\n"
            "Новые операции будут записаны в RUB со списанием с «ozon банк» "
            "и товарными расходами по категориям.\\n"
            "ПСБ и Сбербанк не изменяются.\\n\\n"
            "Перед сохранением будет создана резервная копия .bak."
        )
        if not messagebox.askyesno("Подтверждение импорта", prompt):
            return
        src = Path(self.gnucash_path.get())
        try:
            bank = find_account_by_path(
                account_data(root), "Root Account:Активы:Текущие активы:ozon банк"
            )
            for plan in plans:
                if plan["action"] == "fill":
                    fill_existing_transaction(plan["receipt"], plan["entry"], plan["mapped"])
                else:
                    create_receipt_transaction(root, plan["receipt"], bank, plan["mapped"])

            backup = Path(str(src) + ".bak")
            if not backup.exists():
                shutil.copy2(src, backup)
            fd, temp_name = tempfile.mkstemp(prefix=src.name + ".", suffix=".tmp", dir=str(src.parent))
            os.close(fd)
            try:
                xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                if compressed:
                    with open(temp_name, "wb") as f:
                        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
                            gz.write(xml_bytes)
                else:
                    Path(temp_name).write_bytes(xml_bytes)
                check_raw = Path(temp_name).read_bytes()
                check_xml = gzip.decompress(check_raw) if compressed else check_raw
                ET.fromstring(check_xml)
                os.replace(temp_name, src)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            self.status.set(f"Готово: импортировано чеков — {len(plans)}.")
            self.save_btn.config(state="disabled")
            messagebox.showinfo(
                "Готово",
                f"Импортировано чеков: {len(plans)}\\nНовых операций: {count_new}\\n"
                f"Заполнено незавершённых: {count_fill}\\nРезервная копия: {backup}"
            )
        except Exception as exc:
            messagebox.showerror("Ошибка записи", str(exc))
            self.status.set("Не удалось сохранить. Проверьте резервную копию и файл.")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
