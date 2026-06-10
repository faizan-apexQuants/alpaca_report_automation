# Apex Reports — Client Performance Pipeline

Pulls every client from the GemAlgo Alpaca-open endpoint, renders a personalised
two-page HTML dashboard per client, converts each one to an A4-portrait PDF
with Playwright, and merges them into one master PDF with bookmarks.

```
all-clients-data → map → render HTML → PDF (Playwright) → merge (pypdf)
```

## API

A single call returns every client in one payload:

```
GET https://api.gemalgo.com/api/alpaca-open/all-clients-data?api_code=<API_KEY>
```

Each record contains `customer_profile`, `account_mapping`, `strategy_info`,
`performance_metrics`, `financial_summary` (including `equity_metrics` and
`current_positions`) and `trading_history` (`recent_orders`,
`recent_activities`). The mapper in `data_mapper.py` translates these into the
view-model the template renders.

## Prerequisites

- Python **3.11+**

Install:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Configuration

Copy `.env.example` to `.env` and fill in:

| Variable        | Purpose                                                          |
| --------------- | ---------------------------------------------------------------- |
| `API_BASE_URL`  | `https://api.gemalgo.com/api/alpaca-open` (no trailing slash)    |
| `API_KEY`       | Passed as `?api_code=` to the endpoint                           |
| `OUTPUT_DIR`    | Where per-client and merged PDFs are written (`./out`)           |

## Running

```bash
python run_reports.py                                       # default: monthly (last 30 days)
python run_reports.py --period daily                        # last 1 day
python run_reports.py --period weekly                       # last 7 days
python run_reports.py --period monthly                      # last 30 days
python run_reports.py --period last-month                   # previous full calendar month
python run_reports.py --period all                          # lifetime / since inception
python run_reports.py --period custom --from 2026-03-01 --to 2026-05-31
python run_reports.py --period monthly --month 2025-04      # specific past month
python run_reports.py --client-id 29                        # one client (testing)
python run_reports.py --client "Smith"                      # one client by name (substring)
python run_reports.py --theme yellow                        # alternate theme
python run_reports.py --output-dir ./out/2026-05
```

The `--client-id` value is the `customer_profile.id` from the API payload.

### Reporting periods

| `--period`    | Window                          | P&L source                                              |
| ------------- | ------------------------------- | ------------------------------------------------------- |
| `daily`       | last 1 day                      | `performance_metrics.today_pnl`                         |
| `weekly`      | last 7 days                     | `performance_metrics.weekly_pnl`                        |
| `monthly`     | last 30 days *(default)*        | `performance_metrics.monthly_pnl`                       |
| `last-month`  | previous full calendar month    | FIFO-matched realized P&L over the window               |
| `all`         | lifetime                        | `current_equity − (total_deposits − total_withdrawals)` |
| `custom`      | `[--from, --to]` inclusive      | FIFO-matched realized P&L over the window               |

For every period the **closed trade log** is filtered to the selected window
using `recent_orders[*].created_at`. Open positions are point-in-time state,
so they are shown as-is regardless of period. Output filenames include the
period (e.g. `all_clients_report_2026_05_monthly.pdf`) so different periods
co-exist on disk.

Outputs:

```
<OUTPUT_DIR>/
├── clients/
│   ├── 29_softholding_monthly.pdf
│   └── …
└── all_clients_report_2026_05_monthly.pdf
```

The merged PDF has one bookmark per client for fast navigation.

## What the dashboard shows

* **Page 1 — KPI row** (5 cards): Account Balance · Overall P&L · `<Period>` P&L
  (or *Last 30 Days P&L* when `--period all`) · Floating P&L · Growth. Each is
  colour-coded green/red by sign.
* **Page 1 — Account Composition strip**: Equity · Cash · Long Market Value ·
  Buying Power · Net Capital.
* **Page 1 — Open Position Detail table**: ticker, qty, current price, market
  value.
* **Page 2 — Closed Trade Log**: ticker, side, time, qty, price, notional,
  per-trade realized P&L (FIFO-matched: `(sell − buy) × matched_qty`), status.
  The page-2 tagbar shows the summed realized P&L across all visible orders.

## Scheduling

```cron
0 6 1 * *  cd /opt/apex-reports && /usr/bin/python run_reports.py >> ./out/cron.log 2>&1
```

On Windows, use Task Scheduler with the action
`python C:\path\to\apex-reports\run_reports.py`.

## Project layout

```
apex-reports/
├── .env.example
├── requirements.txt
├── run_reports.py             # CLI orchestrator
├── api_client.py              # GemAlgo HTTP client
├── data_mapper.py             # raw API → view-model
├── dashboard_renderer.py      # Jinja2 renderer
├── pdf_generator.py           # Playwright HTML→PDF
├── pdf_merger.py              # pypdf merge + bookmarks
└── templates/
    └── dashboard.html         # Self-contained dashboard
```

## Troubleshooting

**`playwright._impl._errors.Error: Executable doesn't exist`**
→ `python -m playwright install chromium`.

**Fonts look like Times New Roman in the PDF**
→ The renderer waits up to 8 s for Google Fonts. If your environment blocks
`fonts.googleapis.com`, host the fonts locally.

**One client failure aborts the run**
→ It shouldn't — mapping and render errors are caught per-client and reported
in the summary. Only a failure on the single `/all-clients-data` call (network
or HTTP error) aborts the run.

**Re-running for the same period duplicates files**
→ It does not. Per-client PDFs and the merged PDF are overwritten in place.

## Security notes

- `api_code` and account numbers are never logged.
- All secrets come from `.env`; only `.env.example` is committed.
