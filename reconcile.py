"""Reconciliation engine. Pure logic: Datasets in, a Result out."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from loader import Dataset

TIMING = "TIMING"
CARRY_IN = "TIMING_CARRY_IN"
ROUNDING = "ROUNDING"
DUP_PLATFORM = "DUPLICATE_PLATFORM"
DUP_BANK = "DUPLICATE_BANK"
ORPHAN = "ORPHAN_REFUND"
ID_CONFLICT = "ID_CONFLICT"
AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
PLATFORM_ONLY = "PLATFORM_ONLY"
BANK_ONLY = "BANK_ONLY"

EXPLAINED = [TIMING, CARRY_IN, ROUNDING, DUP_PLATFORM, DUP_BANK, ORPHAN]
UNEXPLAINED = [ID_CONFLICT, AMOUNT_MISMATCH, PLATFORM_ONLY, BANK_ONLY]

BRIDGE_LABELS = {
    TIMING: "Timing: platform in period, bank settled later",
    CARRY_IN: "Timing: bank settled in period, recorded earlier",
    ROUNDING: "Rounding (net of per-line paise differences)",
    DUP_PLATFORM: "Duplicate rows on platform",
    DUP_BANK: "Duplicate rows at bank",
    ORPHAN: "Refunds with no original payment (one-sided only)",
}

ITEM_COLUMNS = [
    "category", "txn_id", "ref_txn_id", "side", "platform_paise",
    "bank_paise", "effect_paise", "explained", "explanation",
]


@dataclass
class Result:
    period: str
    matched: pd.DataFrame
    items: pd.DataFrame
    bridge: pd.DataFrame
    summary: dict
    notes: list[str] = field(default_factory=list)
    data_errors: dict = field(default_factory=dict)


def inr(paise: int) -> str:
    return f"{paise / 100:,.2f}"


def _item(category, rec, side, effect, why, platform_paise=None, bank_paise=None) -> dict:
    return {
        "category": category,
        "txn_id": rec["txn_id"],
        "ref_txn_id": rec["ref_txn_id"],
        "side": side,
        "platform_paise": platform_paise,
        "bank_paise": bank_paise,
        "effect_paise": effect,
        "explained": category in EXPLAINED,
        "explanation": why,
    }


def find_duplicates(df: pd.DataFrame, side: str, max_settle_month: str | None = None):
    df = df.sort_values("src_row", kind="stable")
    keep = df.drop_duplicates("txn_id", keep="first")
    firsts = {r["txn_id"]: r for r in keep.to_dict("records")}
    sign = -1 if side == "platform" else 1
    items = []
    for r in df[df.duplicated("txn_id", keep="first")].to_dict("records"):
        if max_settle_month and r["settle_month"] > max_settle_month:
            continue
        first = firsts[r["txn_id"]]
        same = all(first[k] == r[k] for k in ("amount_paise", "txn_date", "txn_type"))
        amt = r["amount_paise"]
        pp, bp = (amt, None) if side == "platform" else (None, amt)
        if same:
            cat = DUP_PLATFORM if side == "platform" else DUP_BANK
            why = f"{r['txn_id']} appears more than once on the {side} (extra copy of {inr(amt)})"
        else:
            cat = ID_CONFLICT
            why = (
                f"{r['txn_id']} repeated on the {side} with different details "
                f"({inr(first['amount_paise'])} vs {inr(amt)})"
            )
        items.append(_item(cat, r, side, sign * amt, why, pp, bp))
    return keep.reset_index(drop=True), items


def reconcile(platform: Dataset, bank: Dataset, period: str, tolerance_paise: int = 1) -> Result:
    notes: list[str] = []
    p, p_items = find_duplicates(platform.rows, "platform")
    b, b_items = find_duplicates(bank.rows, "bank", max_settle_month=period)
    items = p_items + b_items

    payment_ids = set(p.loc[p.txn_type != "REFUND", "txn_id"]) | set(
        b.loc[b.txn_type != "REFUND", "txn_id"]
    )

    def is_orphan(rec) -> bool:
        return rec["txn_type"] == "REFUND" and rec["ref_txn_id"] not in payment_ids

    p_recs = {r["txn_id"]: r for r in p.to_dict("records")}
    b_recs = {r["txn_id"]: r for r in b.to_dict("records")}
    matched = []

    for tid, pr in p_recs.items():
        br = b_recs.get(tid)
        pa = pr["amount_paise"]
        if br is None:
            if is_orphan(pr):
                items.append(_item(
                    ORPHAN, pr, "platform", -pa,
                    f"Refund of {inr(-pa)} refers to {pr['ref_txn_id'] or '(blank)'}, "
                    "which is not in either dataset; bank has no matching row",
                    pa,
                ))
            else:
                items.append(_item(
                    PLATFORM_ONLY, pr, "platform", -pa,
                    "On the platform but not in the bank statement", pa,
                ))
            continue

        ba = br["amount_paise"]
        diff = pa - ba
        if br["settle_month"] > period:
            status = TIMING
            items.append(_item(
                TIMING, pr, "both", -pa,
                f"Platform settlement {pr['settlement_date']}, bank settlement "
                f"{br['settlement_date']} (after {period})",
                pa, ba,
            ))
        elif diff == 0:
            status = "MATCHED"
        elif abs(diff) <= tolerance_paise:
            status = ROUNDING
            items.append(_item(
                ROUNDING, pr, "both", -diff,
                f"Platform {inr(pa)} vs bank {inr(ba)} (difference {diff:+d} paise)",
                pa, ba,
            ))
        else:
            status = AMOUNT_MISMATCH
            items.append(_item(
                AMOUNT_MISMATCH, pr, "both", -diff,
                f"Platform {inr(pa)} vs bank {inr(ba)}: more than {tolerance_paise} paise apart",
                pa, ba,
            ))
        if is_orphan(pr):
            items.append(_item(
                ORPHAN, pr, "both", 0,
                f"Refund of {inr(-pa)} refers to {pr['ref_txn_id'] or '(blank)'}, which is "
                "in neither dataset. Totals agree, but the original payment needs tracing",
                pa, ba,
            ))
        matched.append({
            "txn_id": tid,
            "txn_type": pr["txn_type"],
            "platform_settlement": pr["settlement_date"],
            "bank_settlement": br["settlement_date"],
            "platform_paise": pa,
            "bank_paise": ba,
            "diff_paise": diff,
            "status": status,
        })

    for tid, br in b_recs.items():
        if tid in p_recs:
            continue
        ba = br["amount_paise"]
        if br["settle_month"] > period:
            notes.append(
                f"{tid} settled {br['settlement_date']} (after {period}) with no platform record: "
                "out of scope, ignored"
            )
        elif is_orphan(br):
            items.append(_item(
                ORPHAN, br, "bank", ba,
                f"Refund of {inr(-ba)} at the bank refers to {br['ref_txn_id'] or '(blank)'}, "
                "which is not in either dataset",
                None, ba,
            ))
        elif br["txn_month"] < period:
            items.append(_item(
                CARRY_IN, br, "bank", ba,
                f"Transaction dated {br['txn_date']} settled by the bank in {period}",
                None, ba,
            ))
        else:
            items.append(_item(
                BANK_ONLY, br, "bank", ba,
                "In the bank statement but not on the platform", None, ba,
            ))

    platform_total = int(platform.rows.amount_paise.sum())
    bank_total_in_period = int(
        bank.rows.loc[bank.rows.settle_month <= period, "amount_paise"].sum()
    )
    bank_total_statement = int(bank.rows.amount_paise.sum())

    items_df = pd.DataFrame(items, columns=ITEM_COLUMNS)
    for c in ("platform_paise", "bank_paise", "effect_paise"):
        items_df[c] = items_df[c].astype("Int64")
    order = {c: i for i, c in enumerate(EXPLAINED + UNEXPLAINED)}
    items_df = (
        items_df.assign(_o=items_df.category.map(order))
        .sort_values(["_o", "txn_id"])
        .drop(columns="_o")
        .reset_index(drop=True)
    )

    explained_effect = {
        c: int(items_df.loc[items_df.category == c, "effect_paise"].sum())
        for c in EXPLAINED
    }
    expected = platform_total + sum(explained_effect.values())
    n_unexplained = int((~items_df.explained).sum())
    unexplained = bank_total_in_period - expected

    rows = [("Platform total", "start", None, platform_total)]
    for c in EXPLAINED:
        rows.append((
            BRIDGE_LABELS[c],
            "adjustment",
            int((items_df.category == c).sum()),
            explained_effect[c],
        ))
    rows += [
        ("Expected bank total", "subtotal", None, expected),
        ("Actual bank total (settled in period)", "actual", None, bank_total_in_period),
        ("Unexplained difference", "result", n_unexplained, unexplained),
    ]
    bridge = pd.DataFrame(rows, columns=["line", "kind", "items", "amount_paise"])
    bridge["items"] = bridge["items"].astype("Int64")

    matched_df = pd.DataFrame(matched, columns=[
        "txn_id", "txn_type", "platform_settlement", "bank_settlement",
        "platform_paise", "bank_paise", "diff_paise", "status",
    ])
    summary = {
        "period": period,
        "tolerance_paise": tolerance_paise,
        "platform_total_paise": platform_total,
        "bank_total_in_period_paise": bank_total_in_period,
        "bank_total_statement_paise": bank_total_statement,
        "unexplained_paise": unexplained,
        "unexplained_items": n_unexplained,
        "matched_pairs": len(matched_df),
        "clean_matches": int((matched_df.status == "MATCHED").sum()),
        "reconciled": unexplained == 0 and n_unexplained == 0,
    }
    return Result(
        period, matched_df, items_df, bridge, summary, notes,
        {"platform": platform.errors, "bank": bank.errors},
    )
