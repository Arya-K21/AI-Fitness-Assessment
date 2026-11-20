from pathlib import Path

import pandas as pd

from loader import clean, load_datasets
from reconcile import (
    AMOUNT_MISMATCH, DUP_BANK, DUP_PLATFORM, ID_CONFLICT, ORPHAN,
    PLATFORM_ONLY, ROUNDING, TIMING, reconcile,
)

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "transactions.csv"


def ds(name, *rows):
    records = []
    for i, (txn_id, txn_date, settlement_date, txn_type, amount, *ref) in enumerate(rows, 1):
        records.append({
            "src_row": i,
            "txn_id": txn_id,
            "ref_txn_id": ref[0] if ref else "",
            "txn_date": txn_date,
            "settlement_date": settlement_date,
            "txn_type": txn_type,
            "amount": str(amount),
        })
    cols = [
        "src_row", "txn_id", "ref_txn_id", "txn_date",
        "settlement_date", "txn_type", "amount",
    ]
    return clean(pd.DataFrame(records, columns=cols), name)


def run(platform, bank, **kwargs):
    return reconcile(platform, bank, "2026-10", **kwargs)


def categories(result):
    return sorted(result.items.category)


def pay(txn_id, amount, settlement="2026-10-06"):
    return (txn_id, "2026-10-05", settlement, "PAYMENT", amount)


def test_clean_match():
    result = run(ds("p", pay("T1", "100.00")), ds("b", pay("T1", "100.00")))
    assert result.items.empty
    assert result.summary["reconciled"]


def test_timing_difference():
    platform = ds("p", ("T1", "2026-10-30", "2026-10-30", "PAYMENT", "7800.00"))
    bank = ds("b", ("T1", "2026-10-30", "2026-11-02", "PAYMENT", "7800.00"))
    result = run(platform, bank)
    assert categories(result) == [TIMING]
    assert result.items.effect_paise.iloc[0] == -780000
    assert result.summary["reconciled"]


def test_rounding_net_difference():
    platform = ds(
        "p",
        pay("T1", "4166.67"),
        pay("T2", "2083.34"),
        pay("T3", "6250.01"),
        pay("T4", "100.00"),
    )
    bank = ds(
        "b",
        pay("T1", "4166.66"),
        pay("T2", "2083.33"),
        pay("T3", "6250.00"),
        pay("T4", "100.00"),
    )
    result = run(platform, bank)
    assert categories(result) == [ROUNDING] * 3
    assert result.items.effect_paise.sum() == -3
    assert result.summary["reconciled"]


def test_amount_mismatch_is_unexplained():
    result = run(
        ds("p", pay("T1", "100.00")),
        ds("b", pay("T1", "99.50")),
    )
    assert categories(result) == [AMOUNT_MISMATCH]
    assert not result.summary["reconciled"]
    assert result.summary["unexplained_paise"] == -50


def test_duplicate_platform_and_bank():
    platform_result = run(
        ds("p", pay("T1", "3450.00"), pay("T1", "3450.00")),
        ds("b", pay("T1", "3450.00")),
    )
    assert categories(platform_result) == [DUP_PLATFORM]
    assert platform_result.summary["platform_total_paise"] == 690000
    assert platform_result.summary["reconciled"]

    bank_result = run(
        ds("p", pay("T1", "5120.00")),
        ds("b", pay("T1", "5120.00"), pay("T1", "5120.00")),
    )
    assert categories(bank_result) == [DUP_BANK]
    assert bank_result.summary["reconciled"]


def test_id_conflict():
    result = run(
        ds("p", pay("T1", "100.00"), pay("T1", "200.00")),
        ds("b", pay("T1", "100.00")),
    )
    assert categories(result) == [ID_CONFLICT]
    assert not result.summary["reconciled"]


def test_orphan_refund():
    orphan = ("R1", "2026-10-26", "2026-10-27", "REFUND", "-1899.00", "T9")
    result = run(ds("p", orphan), ds("b", orphan))
    assert categories(result) == [ORPHAN]
    assert result.items.effect_paise.iloc[0] == 0
    assert result.summary["reconciled"]


def test_unmatched_rows_and_net_zero():
    result = run(
        ds("p", pay("T1", "100.00"), pay("T2", "50.00")),
        ds("b", pay("T1", "100.00")),
    )
    assert categories(result) == [PLATFORM_ONLY]
    assert result.summary["unexplained_paise"] == -5000
    assert not result.summary["reconciled"]

    result = run(
        ds("p", pay("T1", "100.00"), pay("T2", "50.00")),
        ds("b", pay("T1", "100.00"), pay("T3", "50.00")),
    )
    assert result.summary["unexplained_paise"] == 0
    assert result.summary["unexplained_items"] == 2
    assert not result.summary["reconciled"]


def test_loader_rejects_bad_rows():
    dataset = ds(
        "p",
        pay("T1", "100.00"),
        pay("T2", "abc"),
        ("", "2026-10-05", "2026-10-06", "PAYMENT", "5"),
    )
    assert len(dataset.rows) == 1
    assert len(dataset.errors) == 2


def test_loader_reads_stacked_and_source_files(tmp_path):
    stacked = tmp_path / "both.csv"
    stacked.write_text(
        "Dataset 2: Bank settlement statement,,,,,\n"
        "txn_id,ref_txn_id,txn_date,settlement_date,txn_type,amount\n"
        "T1,,2026-10-05,2026-10-06,PAYMENT,99.00\n\n"
        "Dataset 1: Payment platform,,,,,\n"
        "txn_id,ref_txn_id,txn_date,settlement_date,txn_type,amount\n"
        "T1,,2026-10-05,2026-10-06,PAYMENT,100.00\n",
    )
    platform, bank = load_datasets(stacked)
    assert platform.rows.amount_paise.iloc[0] == 10000
    assert bank.rows.amount_paise.iloc[0] == 9900

    source = tmp_path / "src.csv"
    source.write_text(
        "source,txn_id,ref_txn_id,txn_date,settlement_date,txn_type,amount\n"
        "platform,T1,,2026-10-05,2026-10-06,PAYMENT,100.00\n"
        "bank,T1,,2026-10-05,2026-10-06,PAYMENT,100.00\n",
    )
    platform, bank = load_datasets(source)
    assert len(platform.rows) == 1
    assert len(bank.rows) == 1


def test_real_sample_answer_key():
    if not SAMPLE.exists():
        return
    platform, bank = load_datasets(SAMPLE)
    result = run(platform, bank)
    by_category = result.items.groupby("category").effect_paise.agg(["count", "sum"])
    assert by_category.loc[TIMING].tolist() == [3, -1433900]
    assert by_category.loc[ROUNDING].tolist() == [4, -4]
    assert by_category.loc[DUP_PLATFORM].tolist() == [1, -345000]
    assert by_category.loc[DUP_BANK].tolist() == [1, 512000]
    assert by_category.loc[ORPHAN].tolist() == [1, 0]
    assert result.summary["platform_total_paise"] == 13508227
    assert result.summary["bank_total_in_period_paise"] == 12241323
    assert result.summary["reconciled"]
