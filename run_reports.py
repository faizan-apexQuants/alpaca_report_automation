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
    client_grp = p.add_mutually_exclusive_group()
    client_grp.add_argument("--client-id", help="Only run for this client ID (must know the ID)")
    client_grp.add_argument(
        "--client",
        metavar="NAME",
        help="Generate report for a single client by name (case-insensitive substring match)",
    )
    p.add_argument("--no-email", action="store_true", help="Skip email send; PDF stays on disk")
    p.add_argument("--theme", choices=["purple", "yellow"], default="purple")
    p.add_argument(
        "--period",
        choices=["daily", "weekly", "monthly", "3months", "all"],
        default="monthly",
        help="Reporting period (default: monthly). 3months has no API P&L value — KPI shows '—'.",
    )
    p.add_argument("--output-dir", help="Override output dir (default: $OUTPUT_DIR or ./out)")
    p.add_argument(
        "--month",
        help=(
            "Generate a historical monthly report for the given YYYY-MM (e.g. 2025-04). "
            "Only valid with --period monthly. Order log and net-notional chart are scoped "
            "to that calendar month; KPI cards still reflect current account state "
            "(the API does not expose historical snapshots)."
        ),
    )
    return p.parse_args(argv)


def _client_name(rec: dict) -> str:
    """Extract the display name from a raw API record."""
    cp = rec.get("customer_profile") or {}
    first = cp.get("first_name") or ""
    last = cp.get("last_name") or ""
    return f"{first} {last}".strip() or cp.get("username") or cp.get("email") or str(cp.get("id", "?"))


def _resolve_client_name(records: list[dict], query: str) -> str | None:
    """Find a single client ID by case-insensitive substring match on name.

    Returns the client-id string, or None (and prints diagnostics) on
    zero / ambiguous matches.
    """
    q = query.lower()
    matches: list[tuple[str, str]] = []
    for rec in records:
        cp = rec.get("customer_profile") or {}
        cid = str(cp.get("id") or cp.get("username") or "?")
        name = _client_name(rec)
        if q in name.lower():
            matches.append((cid, name))

    if len(matches) == 1:
        cid, name = matches[0]
        log.info("matched client: %s (ID %s)", name, cid)
        return cid
    if len(matches) == 0:
        log.error("no client name matches '%s'", query)
        return None
    # Multiple matches — list them so the user can refine
    log.error("'%s' matched %d clients — be more specific:", query, len(matches))
    for cid, name in sorted(matches, key=lambda e: e[1].lower()):
        print(f"  ID {cid:>5}  {name}")
    return None


def _parse_month(s: str | None) -> tuple[int, int] | None:
    if not s:
        return None
    try:
        d = dt.datetime.strptime(s, "%Y-%m")
    except ValueError as exc:
        raise SystemExit(f"--month must be YYYY-MM (got {s!r}): {exc}")
    return d.year, d.month


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    _setup_logging()
    args = _parse_args(argv)
    month = _parse_month(args.month)
    if month is not None and args.period != "monthly":
        log.error("--month is only valid with --period monthly (got --period %s)", args.period)
        return 2

    output_root = Path(args.output_dir or os.getenv("OUTPUT_DIR") or "./out").resolve()
    per_client_dir = output_root / "clients"
    per_client_dir.mkdir(parents=True, exist_ok=True)

    client_filter = args.client_id

    # --client NAME: fetch all, resolve name → ID, then filter.
    if args.client:
        log.info("fetching clients to resolve name '%s'", args.client)
        try:
            all_records, _ = api_client.fetch_all(client_filter=None)
        except api_client.APIError as exc:
            log.error("aborting: %s", exc)
            return 2
        if not all_records:
            log.error("no clients available")
            return 2
        resolved = _resolve_client_name(all_records, args.client)
        if resolved is None:
            return 2
        client_filter = resolved

    log.info("fetching clients%s", f" (filter: {client_filter})" if client_filter else "")
    try:
        raw_records, skipped = api_client.fetch_all(client_filter=client_filter)
    except api_client.APIError as exc:
        log.error("aborting: %s", exc)
        return 2

    if not raw_records:
        log.error("no clients to process")
        return 2

    now = dt.datetime.now()
    if month is not None:
        ym = f"{month[0]:04d}_{month[1]:02d}"
        subject_month = dt.datetime(month[0], month[1], 1).strftime("%B %Y")
        # Best-effort: attach a point-in-time portfolio snapshot to each client record.
        # If the endpoint isn't available we proceed without it; data_mapper degrades
        # gracefully (period_pnl reconstructed from orders, KPIs marked as current).
        api = api_client.APIClient()
        start_dt, end_dt = data_mapper._month_window(*month)
        start_iso = start_dt.date().isoformat()
        end_iso = end_dt.date().isoformat()
        hit = miss = 0
        for raw in raw_records:
            acct = ((raw.get("account_mapping") or {}).get("account_number") or "").strip()
            if not acct:
                miss += 1
                continue
            snap = api.fetch_portfolio_history(acct, start_iso, end_iso)
            if snap:
                raw["_historical_snapshot"] = snap
                hit += 1
            else:
                miss += 1
        log.info("historical snapshot: %d hits, %d misses (reconstructing from orders)", hit, miss)
    else:
        ym = now.strftime("%Y_%m")
        subject_month = now.strftime("%B %Y")
    merged_pdf = output_root / f"all_clients_report_{ym}_{args.period}.pdf"

    merge_entries: list[tuple[str, Path]] = []
    processed: list[str] = []

    with pdf_generator.browser_session() as browser:
        for idx, raw in enumerate(tqdm(raw_records, desc="rendering", unit="client"), start=1):
            try:
                client, perf = data_mapper.map_record(raw, now=now, period=args.period, month=month)
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
