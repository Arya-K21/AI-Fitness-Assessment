"""Command line reconciliation runner."""
import argparse

from loader import load_datasets
from reconcile import reconcile
from report import bridge_text, export_excel


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile platform transactions against bank settlements"
    )
    parser.add_argument(
        "files", nargs="+",
        help="one combined CSV, or platform CSV then bank CSV",
    )
    parser.add_argument("--period", default="2026-10", help="reporting month, YYYY-MM")
    parser.add_argument(
        "--tolerance", type=int, default=1,
        help="per-line rounding tolerance in paise",
    )
    parser.add_argument(
        "--out", default="reconciliation_report.xlsx",
        help="Excel report path",
    )
    args = parser.parse_args()

    platform, bank = load_datasets(*args.files)
    for dataset in (platform, bank):
        if dataset.errors:
            print(
                f"Warning: {len(dataset.errors)} {dataset.name} row(s) rejected, "
                "see the data errors sheet"
            )
    result = reconcile(platform, bank, args.period, args.tolerance)
    print(bridge_text(result))
    export_excel(result, args.out)
    print(f"\nExcel report written to {args.out}")
    return 0 if result.summary["reconciled"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
