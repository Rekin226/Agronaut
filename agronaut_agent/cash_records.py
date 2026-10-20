"""Cycle-attributed actual cash receipts/payments, with exact decimal amounts."""

import re
from decimal import Decimal, localcontext

from agro_model.validation import choice, text

from .field_io import _date, _mapping


def cash_data(data):
    data = dict(_mapping(data, ("entry", "day", "direction", "amount", "currency", "category", "description",
                               "allocation_note", "source", "provenance"), "cash entry"))
    for key in ("entry", "currency", "category", "description", "allocation_note", "source"):
        data[key] = text(key, data.get(key)).strip()
    _date(data.get("day"))
    choice("direction", data.get("direction"), ("expense", "income"))
    choice("provenance", data.get("provenance"), ("documented", "reported", "estimated"))
    if not re.fullmatch(r"[A-Z]{3}", data["currency"]):
        raise ValueError("currency must be three uppercase letters, such as USD or XOF")
    amount = data.get("amount")
    if not isinstance(amount, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,6})?", amount):
        raise ValueError("amount must be a positive plain decimal string, with at most 15 integer and 6 fractional digits")
    if Decimal(amount) <= 0:
        raise ValueError("amount must be positive; use direction for payments versus receipts")
    return data


def cash_totals(records):
    groups, categories = {}, {}
    # Precision exceeds the maximum per-entry amount plus any practical journal size.
    with localcontext() as context:
        context.prec = 50
        for row in records:
            if row["kind"] != "cash" or row["voided"] or row["superseded"]:
                continue
            data = row["data"]
            key = data["currency"], data["provenance"]
            group = groups.setdefault(key, dict(currency=key[0], provenance=key[1], record_ids=[],
                recorded_income=Decimal(0), recorded_expense=Decimal(0), income_entries=0, expense_entries=0,
                first_day=data["day"], last_day=data["day"]))
            group["recorded_"+data["direction"]] += Decimal(data["amount"])
            group[data["direction"]+"_entries"] += 1
            group["record_ids"].append(row["id"])
            group["first_day"] = min(group["first_day"], data["day"])
            group["last_day"] = max(group["last_day"], data["day"])
            category_key = (*key, data["direction"], data["category"])
            category = categories.setdefault(category_key, dict(currency=key[0], provenance=key[1],
                direction=data["direction"], category=data["category"], amount=Decimal(0), record_ids=[]))
            category["amount"] += Decimal(data["amount"])
            category["record_ids"].append(row["id"])
        totals = [dict(g, recorded_income=format(g["recorded_income"], "f"), recorded_expense=format(g["recorded_expense"], "f"),
                       recorded_net_cash=format(g["recorded_income"]-g["recorded_expense"], "f")) for _, g in sorted(groups.items())]
        breakdown = [dict(c, amount=format(c["amount"], "f"), entries=len(c["record_ids"])) for _, c in sorted(categories.items())]
    return dict(totals=totals, by_category=breakdown,
        note="Recorded cash only, separated by currency and provenance. No entries means unknown cash history. "
             "Zero subtotals mean no recorded receipts/payments in that group, not proof there were none. "
             "Recorded net cash is receipts minus payments, not profit. No exchange-rate conversion, accruals, depreciation, "
             "inventory valuation or inferred unpaid costs. Documented means the operator supplied a document source, not an audit. "
             "Entries can predate planting or follow crop closure; their cash-date ranges are shown separately. "
             "Only the stated amount allocated to this cycle is counted; shared transaction allocations are not reconciled across cycles.")
