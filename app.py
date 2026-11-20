"""Streamlit demo UI for payment reconciliation."""
import io
from pathlib import Path

import streamlit as st

from loader import load_datasets
from reconcile import (
    CARRY_IN, DUP_BANK, DUP_PLATFORM, ORPHAN, ROUNDING, TIMING,
    UNEXPLAINED, inr, reconcile,
)
from report import export_excel, to_inr

SAMPLE = Path("data/transactions.csv")

st.set_page_config(page_title="Payment reconciliation", layout="wide")
st.title("Payment reconciliation")
st.caption("Platform records vs bank settlements: every difference is classified and explained.")

with st.sidebar:
    period = st.text_input("Reporting period (YYYY-MM)", "2026-10")
    tolerance = st.number_input(
        "Rounding tolerance per line (paise)", min_value=0, value=1, step=1
    )

uploads = st.file_uploader(
    "Upload one CSV holding both datasets, or the platform CSV and the bank CSV",
    type="csv",
    accept_multiple_files=True,
)
if uploads:
    sources = uploads
elif SAMPLE.exists() and st.checkbox(f"Use sample data ({SAMPLE})"):
    sources = [SAMPLE]
else:
    st.info("Upload your CSV file(s) to begin.")
    st.stop()

try:
    platform, bank = load_datasets(*sources)
    result = reconcile(platform, bank, period, int(tolerance))
except ValueError as exc:
    st.error(str(exc))
    st.stop()

summary = result.summary
metric_columns = st.columns(4)
metric_columns[0].metric("Platform total", inr(summary["platform_total_paise"]))
metric_columns[1].metric(
    "Bank total (settled in period)",
    inr(summary["bank_total_in_period_paise"]),
)
metric_columns[2].metric(
    "Matched exactly",
    f"{summary['clean_matches']} / {summary['matched_pairs']}",
)
metric_columns[3].metric(
    "Unexplained difference",
    inr(summary["unexplained_paise"]),
)

if summary["reconciled"]:
    st.success("Reconciled: every difference between the two datasets is explained.")
else:
    st.error(
        f"Not reconciled: {summary['unexplained_items']} unexplained item(s), "
        f"difference {inr(summary['unexplained_paise'])}."
    )
for note in result.notes:
    st.warning(note)

st.subheader("Bridge: platform total to bank total")
st.dataframe(to_inr(result.bridge).drop(columns="kind"), hide_index=True)

items = result.items
tabs = st.tabs([
    "Timing", "Rounding", "Duplicates", "Orphan refunds",
    "Unexplained", "Matched", "Data errors",
])
groups = [
    (TIMING, CARRY_IN),
    (ROUNDING,),
    (DUP_PLATFORM, DUP_BANK),
    (ORPHAN,),
    tuple(UNEXPLAINED),
]
for tab, categories in zip(tabs, groups):
    with tab:
        subset = items[items.category.isin(categories)]
        if subset.empty:
            st.write("None found.")
        else:
            st.dataframe(to_inr(subset), hide_index=True)
            if categories == (ROUNDING,):
                st.write(
                    f"Net rounding effect: **{inr(int(subset.effect_paise.sum()))}** "
                    f"across {len(subset)} lines."
                )
with tabs[5]:
    st.dataframe(to_inr(result.matched), hide_index=True)
with tabs[6]:
    errors = result.data_errors["platform"] + result.data_errors["bank"]
    st.dataframe(errors, hide_index=True) if errors else st.write("No rows were rejected.")

buffer = io.BytesIO()
export_excel(result, buffer)
st.download_button(
    "Download Excel report",
    buffer.getvalue(),
    file_name=f"reconciliation_{period}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
