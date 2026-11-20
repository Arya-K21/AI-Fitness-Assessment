"""Load, validate and normalize payment and bank datasets."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

REQUIRED = ["txn_id", "txn_date", "settlement_date", "txn_type", "amount"]
VALID_TYPES = {"PAYMENT", "REFUND"}
OUT_COLS = [
    "src_row", "txn_id", "ref_txn_id", "txn_date", "settlement_date",
    "txn_type", "amount_paise", "txn_month", "settle_month",
]

ALIASES = {
    "transaction_id": "txn_id",
    "id": "txn_id",
    "ref": "ref_txn_id",
    "ref_id": "ref_txn_id",
    "reference": "ref_txn_id",
    "original_txn_id": "ref_txn_id",
    "transaction_date": "txn_date",
    "date": "txn_date",
    "settled_date": "settlement_date",
    "settlement": "settlement_date",
    "type": "txn_type",
    "transaction_type": "txn_type",
    "amount_inr": "amount",
}


@dataclass
class Dataset:
    name: str
    rows: pd.DataFrame
    errors: list[dict] = field(default_factory=list)


def _norm_header(value: str) -> str:
    value = value.strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(value, value)


def _read_text(source) -> str:
    if isinstance(source, (str, Path)):
        return Path(source).read_text(encoding="utf-8-sig")
    data = source.getvalue() if hasattr(source, "getvalue") else source.read()
    return data.decode("utf-8-sig") if isinstance(data, bytes) else data


def _split_segments(text: str) -> list[dict]:
    segments = []
    hint = ""
    current = None
    for cells in csv.reader(io.StringIO(text)):
        cells = [c.strip() for c in cells]
        filled = [c for c in cells if c]
        if not filled:
            continue
        if "txn_id" in [_norm_header(c) for c in cells]:
            current = {"hint": hint, "header": [_norm_header(c) for c in cells], "rows": []}
            segments.append(current)
            hint = ""
        elif len(filled) == 1 and " " in filled[0]:
            hint = filled[0].lower()
            current = None
        elif current is not None:
            current["rows"].append(cells)
    return segments


def _segment_frame(segment: dict) -> pd.DataFrame:
    width = len(segment["header"])
    rows = [(r + [""] * width)[:width] for r in segment["rows"]]
    df = pd.DataFrame(rows, columns=segment["header"], dtype=str)
    df.insert(0, "src_row", range(1, len(df) + 1))
    return df


def _role(hint: str) -> str:
    has_bank = "bank" in hint
    has_platform = "platform" in hint
    if has_bank and not has_platform:
        return "bank"
    if has_platform and not has_bank:
        return "platform"
    return ""


def load_datasets(*sources) -> tuple[Dataset, Dataset]:
    segments = []
    for source in sources:
        source_segments = _split_segments(_read_text(source))
        if len(source_segments) == 1 and not source_segments[0]["hint"]:
            source_segments[0]["hint"] = str(getattr(source, "name", source)).lower()
        segments += source_segments

    if len(segments) == 1 and "source" in segments[0]["header"]:
        return _split_by_source_column(_segment_frame(segments[0]))
    if len(segments) != 2:
        raise ValueError(
            f"Expected 2 datasets (platform and bank) but found {len(segments)}. "
            "Provide one combined CSV or two CSVs, each with a txn_id header row."
        )

    roles = [_role(segment["hint"]) for segment in segments]
    if roles[0] == "bank" or roles[1] == "platform":
        segments = segments[::-1]
    platform, bank = (
        clean(_segment_frame(segment), name)
        for segment, name in zip(segments, ("platform", "bank"))
    )
    return platform, bank


def _split_by_source_column(df: pd.DataFrame) -> tuple[Dataset, Dataset]:
    src = df["source"].str.lower()
    platform = df[src.str.contains("platform")]
    bank = df[src.str.contains("bank")]
    if platform.empty or bank.empty:
        raise ValueError("The 'source' column must contain both 'platform' and 'bank' values.")
    return (
        clean(platform.drop(columns="source"), "platform"),
        clean(bank.drop(columns="source"), "bank"),
    )


def _to_paise(value) -> int:
    text = str(value).replace("₹", "").replace("INR", "").replace(",", "").strip()
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"invalid amount '{value}'") from None
    if not amount.is_finite():
        raise ValueError(f"invalid amount '{value}'")
    return int((amount * 100).to_integral_value())


def _to_iso(value, column: str) -> str:
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise ValueError(
            f"invalid {column} '{value}' (expected YYYY-MM-DD)"
        ) from None


def _clean_row(record: dict) -> dict:
    txn_id = str(record.get("txn_id", "")).strip().upper()
    if not txn_id:
        raise ValueError("missing txn_id")
    txn_type = str(record.get("txn_type", "")).strip().upper()
    if txn_type not in VALID_TYPES:
        raise ValueError(f"unknown txn_type '{txn_type}'")
    txn_date = _to_iso(record.get("txn_date", ""), "txn_date")
    settle = _to_iso(record.get("settlement_date", ""), "settlement_date")
    return {
        "src_row": record.get("src_row"),
        "txn_id": txn_id,
        "ref_txn_id": str(record.get("ref_txn_id", "") or "").strip().upper(),
        "txn_date": txn_date,
        "settlement_date": settle,
        "txn_type": txn_type,
        "amount_paise": _to_paise(record.get("amount", "")),
        "txn_month": txn_date[:7],
        "settle_month": settle[:7],
    }


def clean(raw: pd.DataFrame, name: str) -> Dataset:
    missing = [column for column in REQUIRED if column not in raw.columns]
    if missing:
        raise ValueError(f"{name}: missing required column(s): {', '.join(missing)}")
    good = []
    errors = []
    for record in raw.to_dict("records"):
        try:
            good.append(_clean_row(record))
        except ValueError as exc:
            errors.append({**record, "reason": str(exc)})
    return Dataset(name, pd.DataFrame(good, columns=OUT_COLS), errors)
