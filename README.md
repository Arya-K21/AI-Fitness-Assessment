# Payment Reconciliation

A Python tool for reconciling payment-platform transactions against bank
settlements. It normalizes amounts to integer paise, classifies differences,
shows a reconciliation bridge, and exports a multi-sheet Excel report.

## Features

- Accepts a combined CSV with separate header sections, or a
  combined CSV with a `source` column.
- Validates transaction IDs, types, dates, and amounts; invalid rows are
  reported instead of stopping the entire reconciliation.
- Identifies settlement timing, rounding differences, duplicate IDs, orphan
  refunds, and unexplained differences.
- Provides a command-line runner and a Streamlit web interface.
- Exports the bridge and detail categories to an Excel workbook.

## Requirements

- Python 3.10 or later
- Dependencies listed in `requirements.txt`

## Install

From the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Input CSV format

The required columns are:

| Column | Description |
| --- | --- |
| `txn_id` | Transaction identifier |
| `txn_date` | Transaction date in `YYYY-MM-DD` format |
| `settlement_date` | Settlement date in `YYYY-MM-DD` format |
| `txn_type` | `PAYMENT` or `REFUND` |
| `amount` | Amount in rupees; refunds should be negative |

`ref_txn_id` is optional and identifies the original payment for a refund.
Common header aliases such as `transaction_id`, `transaction_date`,
`settled_date`, and `amount_inr` are accepted.

Amounts can include rupee symbols, `INR`, and comma separators. CSVs can be
provided in any of these forms:

1. Two files, one for the platform and one for the bank.
2. One file with two header sections, optionally titled with "platform" and
   "bank" so the loader can identify each section.
3. One file with a `source` column containing `platform` and `bank`.

## Command line

Run reconciliation for October 2026 using the bundled combined sample:

```powershell
python cli.py data/transactions.csv --period 2026-10 --out reconciliation_report.xlsx
```

Or provide the two input files directly:

```powershell
python cli.py "platform.csv" "bank.csv" --period 2026-10 --tolerance 1 --out report.xlsx
```

The command prints the reconciliation bridge and writes an Excel workbook.
Its exit code is `0` when the result is reconciled and `1` when unexplained
items remain.

## Streamlit web app

Start the local app with:

```powershell
python -m streamlit run app.py
```

Upload one combined CSV or both source CSVs in the app. It displays the
reconciliation summary and offers the workbook for download.

## Tests

Run the test suite with:

```powershell
python -m pytest
```

## Deploy on Streamlit Community Cloud

The app is configured to run from the repository root with `app.py` and
installs its Python dependencies from `requirements.txt`. To publish it:

1. Push this repository to GitHub.
2. Sign in to [Streamlit Community Cloud](https://share.streamlit.io/) using
   the GitHub account that can access the repository.
3. Choose **Create app**, then select `Arya-K21/AI-Fitness-Assessment`,
   branch `main`, and main file path `app.py`.
4. Choose **Deploy**. Streamlit installs `requirements.txt` and provides a
   public app URL. Later pushes to `main` are deployed automatically.

The bundled CSV is demonstration data. Do not commit real transaction data,
credentials, or generated reports to a public repository. Uploaded files are
processed by the app while it is running; do not upload sensitive data unless
your organization has approved this hosting environment.

## Repository layout

```text
loader.py                 CSV loading, validation, and normalization
reconcile.py              Reconciliation rules and bridge calculation
report.py                 Text bridge and Excel export
cli.py                    Command-line interface
app.py                    Streamlit interface
data/transactions.csv     Combined demonstration dataset
tests/test_reconcile.py   Automated tests
```
