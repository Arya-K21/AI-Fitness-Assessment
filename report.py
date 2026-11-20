"""Turn a Result into a text bridge and Excel workbook."""
from __future__ import annotations

import pandas as pd

from reconcile import (
    CARRY_IN, DUP_BANK, DUP_PLATFORM, ORPHAN, ROUNDING, TIMING,
    UNEXPLAINED, Result, inr,
)


def to_inr(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in [c for c in out.columns if c.endswith("_paise")]:
        pos = out.columns.get_loc(col)
        out.insert(
            pos,
            col[:-6] + "_inr",
            (out[col].astype("float64") / 100).round(2),
        )
        out = out.drop(columns=col)
    return out


def sheets(result: Result) -> dict[str, pd.DataFrame]:
    items = result.items

    def pick(*categories) -> pd.DataFrame:
        return to_inr(
            items[items.category.isin(categories)].reset_index(drop=True)
        )

    rounding = pick(ROUNDING)
    if not rounding.empty:
        rounding = rounding.astype({"explained": "object"})
        rounding.loc[len(rounding), ["txn_id", "effect_inr"]] = [
            "NET (all lines)",
            round(rounding.effect_inr.sum(), 2),
        ]
    return {
        "Bridge": to_inr(result.bridge),
        "Timing": pick(TIMING, CARRY_IN),
        "Rounding": rounding,
        "Duplicates": pick(DUP_PLATFORM, DUP_BANK),
        "Orphan refunds": pick(ORPHAN),
        "Unexplained": pick(*UNEXPLAINED),
        "Matched": to_inr(result.matched),
    }


def bridge_text(result: Result) -> str:
    width = max(len(line) for line in result.bridge.line) + 2
    lines = [f"Reconciliation for {result.period}", "=" * (width + 22)]
    for row in result.bridge.itertuples():
        count = "" if pd.isna(row.items) else f"[{row.items}]"
        lines.append(f"{row.line:<{width}}{count:>5}{inr(row.amount_paise):>17}")
    summary = result.summary
    lines += [
        "=" * (width + 22),
        f"{summary['clean_matches']} of {summary['matched_pairs']} matched pairs agree exactly.",
        "RECONCILED: every difference is explained."
        if summary["reconciled"]
        else f"NOT RECONCILED: {summary['unexplained_items']} unexplained item(s), see the Unexplained sheet.",
    ]
    lines += [f"Note: {note}" for note in result.notes]
    return "\n".join(lines)


def export_excel(result: Result, target) -> None:
    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        for name, df in sheets(result).items():
            df.to_excel(writer, sheet_name=name, index=False)
        for side, errors in result.data_errors.items():
            if errors:
                pd.DataFrame(errors).to_excel(
                    writer, sheet_name=f"{side} data errors"[:31], index=False
                )
        for worksheet in writer.book.worksheets:
            for column in worksheet.columns:
                longest = max(
                    len(str(cell.value)) if cell.value is not None else 0
                    for cell in column
                )
                worksheet.column_dimensions[column[0].column_letter].width = min(
                    longest + 2, 70
                )
