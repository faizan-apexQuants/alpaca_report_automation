"""Orchestrator: fetch → map → render → PDF → merge → email."""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from tqdm import tqdm

import api_client
import dashboard_renderer
import data_mapper
import email_sender
import pdf_generator
import pdf_merger

log = logging.getLogger("run_reports")


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(s)).strip("_") or "client"


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate and email monthly client trading reports")
    p.add_argument("--client-id", help="Only run for this client (testing)")
    p.add_argument("--no-email", action="store_true", help="Skip email send; PDF stays on disk")
    p.add_argument("--theme", choices=["purple", "yellow"], default="purple")
    p.add_argument(
        "--period",
        choices=["daily", "weekly", "monthly", "3months", "all"],
        default="monthly",
        help="Reporting period (default: monthly). 3months has no API P&L value — KPI shows '—'.",
    )
    p.add_argument("--output-dir", help="Override output dir (default: $OUTPUT_DIR or ./out)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    _setup_logging()
    args = _parse_args(argv)

    output_root = Path(args.output_dir or os.getenv("OUTPUT_DIR") or "./out").resolve()
    per_client_dir = output_root / "clients"
    per_client_dir.mkdir(parents=True, exist_ok=True)

    log.info("fetching clients%s", f" (filter: {args.client_id})" if args.client_id else "")
    try:
        raw_records, skipped = api_client.fetch_all(client_filter=args.client_id)
    except api_client.APIError as exc:
        log.error("aborting: %s", exc)
        return 2

    if not raw_records:
        log.error("no clients to process")
        return 2

    now = dt.datetime.now()
    ym = now.strftime("%Y_%m")
    subject_month = now.strftime("%B %Y")
    merged_pdf = output_root / f"all_clients_report_{ym}_{args.period}.pdf"

    merge_entries: list[tuple[str, Path]] = []
    processed: list[str] = []

    with pdf_generator.browser_session() as browser:
        for idx, raw in enumerate(tqdm(raw_records, desc="rendering", unit="client"), start=1):
            try:
                client, perf = data_mapper.map_record(raw, now=now, period=args.period)
            except Exception as exc:  # noqa: BLE001
                cid = (raw.get("customer_profile") or {}).get("id", "?")
                log.error("client %s mapping failed: %s", cid, exc)
                skipped.append((str(cid), f"mapping: {exc}"))
                continue
            cid = client["client_id"]
            slug = _slug(client.get("name") or cid)
            try:
                html = dashboard_renderer.render(client, perf, theme=args.theme, page_num=idx)
                pdf_path = per_client_dir / f"{cid}_{slug}_{args.period}.pdf"
                pdf_generator.render_pdf(browser, html, pdf_path)
                title = f"{client['name']} — {client['strategy']}".strip(" —")
                merge_entries.append((title, pdf_path))
                processed.append(cid)
            except Exception as exc:  # noqa: BLE001
                log.error("client %s render/pdf failed: %s", cid, exc)
                skipped.append((cid, f"render/pdf: {exc}"))

    if not merge_entries:
        log.error("no PDFs were produced; aborting before merge")
        return 3

    log.info("merging %d PDFs → %s", len(merge_entries), merged_pdf)
    pdf_merger.merge_pdfs(merge_entries, merged_pdf)
    size_kb = merged_pdf.stat().st_size / 1024

    if args.no_email:
        email_status = "skipped (--no-email)"
    else:
        skipped_lines = "\n".join(f"  - {cid}: {reason}" for cid, reason in skipped) or "  (none)"
        period_label = data_mapper.PERIODS[args.period][0]
        body = (
            f"{period_label} trading performance reports generated {subject_month}.\n\n"
            f"Reporting period: {period_label}\n"
            f"Clients included: {len(merge_entries)}\n"
            f"File size: {size_kb:,.1f} KB\n"
            f"Attachment: {merged_pdf.name}\n\n"
            f"Skipped clients:\n{skipped_lines}\n"
        )
        try:
            email_sender.send_report(
                merged_pdf,
                subject=f"Client Performance Reports ({period_label}) — {subject_month}",
                body=body,
            )
            email_status = "sent"
        except Exception as exc:  # noqa: BLE001
            log.error("email send failed: %s", exc)
            email_status = f"FAILED: {exc}"

    print()
    print("=" * 64)
    print(f"Period:     {data_mapper.PERIODS[args.period][0]} ({args.period})")
    print(f"Processed:  {len(processed)} clients")
    print(f"Skipped:    {len(skipped)} clients")
    for cid, reason in skipped:
        print(f"  - {cid}: {reason}")
    print(f"Output:     {merged_pdf}")
    print(f"Size:       {size_kb:,.1f} KB")
    print(f"Email:      {email_status}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
