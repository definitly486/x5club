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

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Импорт чеков X5 в GnuCash")
        self.root.geometry("1050x700")
        self.gnucash_path = tk.StringVar()
        self.json_path = tk.StringVar()
        self.status = tk.StringVar(value="Выберите файл GnuCash и файл с чеками.")
        self.analysis = None
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
        ttk.Button(outer, text="Проверить и найти чек", command=self.analyze).grid(row=2, column=0, columnspan=3, sticky="ew", pady=10)
        self.info = tk.Text(outer, height=8, wrap="word")
        self.info.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=5)
        cols = ("item", "account", "amount")
        self.table = ttk.Treeview(outer, columns=cols, show="headings", height=14)
        self.table.heading("item", text="Товар из чека")
        self.table.heading("account", text="Счёт GnuCash")
        self.table.heading("amount", text="Сумма, RUB")
        self.table.column("item", width=470)
        self.table.column("account", width=300)
        self.table.column("amount", width=120, anchor="e")
        self.table.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=5)
        ttk.Label(outer, textvariable=self.status).grid(row=5, column=0, columnspan=3, sticky="w", pady=6)
        self.save_btn = ttk.Button(outer, text="Создать проводки и сохранить GnuCash", command=self.save, state="disabled")
        self.save_btn.grid(row=6, column=0, columnspan=3, sticky="ew", pady=8)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(3, weight=0)
        outer.rowconfigure(4, weight=1)

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
            root, compressed = read_gnucash(self.gnucash_path.get())
            receipts = load_receipts(self.json_path.get())
            accounts = account_data(root)

            bank = find_account_by_path(
                accounts, "Root Account:Активы:Текущие активы:ozon банк"
            )
            discrepancy = find_account_by_path(
                accounts, "Root Account:Дисбаланс-RUB"
            )
            pending = find_pending_transactions(root, discrepancy["id"], bank["id"])
            balance = sum((Decimal(x["amount_cents"]) / 100 for x in pending), Decimal("0"))

            parent = find_product_parent(accounts)
            children = product_accounts(accounts, parent["id"])
            self.log(f"Автоматически загружено категорий-алиасов: {len(children)}")
            self.log("Категории: " + (", ".join(sorted(a["name"] for a in children)) or "нет"))
            self.log(f"Банковский счёт: {bank['path']}")
            self.log(f"Счёт дисбаланса: {discrepancy['path']}")
            self.log(f"Сумма незаполненных проводок Ozon: {balance:.2f} RUB")
            self.log(f"Найдено исходных проводок для заполнения: {len(pending)}")
            self.log(f"Счёт товаров: {parent['path']}")

            matches = []
            for receipt in receipts:
                amount = cents(receipt.get("amountPromo", 0))
                for entry in pending:
                    if entry["amount_cents"] == amount:
                        matches.append((receipt, entry))

            if not matches:
                self.log("Не найден чек с суммой, совпадающей с незаполненной проводкой Ozon Банка.")
                self.log("Суммы исходных проводок: " + (", ".join(f"{Decimal(x['amount_cents'])/100:.2f}" for x in pending) or "нет"))
                self.log("Суммы чеков JSON: " + ", ".join(
                    f"{money(r.get('amountPromo', 0)):.2f} (чек {r.get('checkNum')})" for r in receipts
                ))
                self.status.set("Совпадение не найдено. GnuCash не изменён.")
                return
            if len(matches) > 1:
                self.log("Найдено несколько совпадений по сумме; автоматическая запись запрещена:")
                for r, e in matches:
                    self.log(f"  чек №{r.get('checkNum')} / проводка {e['date']} / {money(r['amountPromo']):.2f}")
                self.status.set("Неоднозначное совпадение. Ничего не записано.")
                return

            receipt, entry = matches[0]
            mapped = []
            missing = []
            sum_items = 0
            for item in receipt.get("items", []):
                amount = cents(item.get("pricePromo", item.get("priceRegular", "0")))
                sum_items += amount
                account = map_item(item, children)
                if account is None:
                    missing.append(item.get("name", "(без названия)"))
                else:
                    mapped.append((item, account))
                    self.table.insert("", "end", values=(
                        item.get("name", ""), account["name"], f"{Decimal(amount)/100:.2f}"
                    ))

            self.log(f"Выбран чек №{receipt.get('checkNum')} от {receipt.get('created')}")
            self.log(f"Сумма чека: {money(receipt.get('amountPromo')):.2f} RUB")
            self.log(f"Исходная банковская проводка: {entry['date']} — {money(Decimal(entry['amount_cents'])/100):.2f} RUB")
            self.log("Будет изменена именно эта проводка: split Ozon Банка останется на месте, split дисбаланса заменится товарами.")

            if missing:
                self.log("Не удалось сопоставить товары:")
                for name in missing:
                    self.log("  • " + name)
                self.status.set("Есть несопоставленные товары. Ничего не записано.")
                return
            if sum_items != cents(receipt.get("amountPromo", 0)):
                self.log(f"ОШИБКА: сумма товаров {Decimal(sum_items)/100:.2f} не равна сумме чека.")
                self.status.set("Контрольная сумма не сошлась. Ничего не записано.")
                return
            if sum_items != entry["amount_cents"]:
                self.log("ОШИБКА: сумма товаров не равна сумме исходной банковской проводки.")
                self.status.set("Суммы не совпадают. Ничего не записано.")
                return

            self.analysis = (root, compressed, receipt, entry, mapped)
            self.log("Все проверки пройдены. Новая транзакция создаваться НЕ будет.")
            self.status.set("Готово к заполнению существующей проводки Ozon Банка.")
            self.save_btn.config(state="normal")
        except Exception as exc:
            messagebox.showerror("Ошибка проверки", str(exc))
            self.status.set("Ошибка. Файл не изменён.")

    def save(self):
        if not self.analysis:
            return
        root, compressed, receipt, entry, mapped = self.analysis
        prompt = (
            f"Заполнить существующую проводку Ozon Банка для чека №{receipt.get('checkNum')} "
            f"на сумму {money(receipt['amountPromo']):.2f} RUB?\n\n"
            "Новая транзакция создаваться не будет.\n"
            "Положительный split «Дисбаланс-RUB» будет заменён товарными расходами.\n"
            "Отрицательный split счёта Ozon Банка, дата и GUID исходной проводки сохранятся.\n\n"
            "Перед сохранением будет создана резервная копия .bak."
        )
        if not messagebox.askyesno("Подтверждение заполнения", prompt):
            return
        src = Path(self.gnucash_path.get())
        try:
            fill_existing_transaction(receipt, entry, mapped)
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
            self.status.set(f"Готово: заполнена исходная проводка чека №{receipt.get('checkNum')}.")
            self.save_btn.config(state="disabled")
            messagebox.showinfo("Готово", f"Заполнена существующая проводка Ozon Банка для чека №{receipt.get('checkNum')}.\nРезервная копия: {backup}")
        except Exception as exc:
            messagebox.showerror("Ошибка записи", str(exc))
            self.status.set("Не удалось сохранить. Проверьте резервную копию и файл.")

def main():
    root = tk.Tk()
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
