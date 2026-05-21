# Apex Reports — Monthly Client Performance Pipeline

Pulls every client from the GemAlgo Alpaca-open endpoint, renders a personalised
single-page HTML dashboard per client, converts each one to an A4-landscape PDF
with Playwright, merges them into one master PDF with bookmarks, and emails it.

```
all-clients-data → map → render HTML → PDF (Playwright) → merge (pypdf) → SMTP
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
- An SMTP account (Gmail app-password, Postmark, SES, etc.)

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
| `SMTP_HOST`     | SMTP server host                                                 |
| `SMTP_PORT`     | `465` (SSL) or `587` (STARTTLS)                                  |
| `SMTP_USER`     | SMTP username                                                    |
| `SMTP_PASS`     | SMTP password / app-password                                     |
| `SMTP_USE_TLS`  | `true` (default) — STARTTLS for port 587                         |
| `EMAIL_FROM`    | From address shown on the message                                |
| `EMAIL_TO`      | Recipient of the merged report                                   |
| `OUTPUT_DIR`    | Where per-client and merged PDFs are written (`./out`)           |

## Running

```bash
python run_reports.py                            # default: monthly
python run_reports.py --period daily             # last 1 day
python run_reports.py --period weekly            # last 7 days
python run_reports.py --period monthly           # last 30 days
python run_reports.py --period 3months           # last 90 days  (KPI P&L shows '—' — not in API)
python run_reports.py --period all               # lifetime / since inception
python run_reports.py --client-id 29             # one client (testing)
python run_reports.py --no-email                 # produce PDF only
python run_reports.py --theme yellow             # alternate theme
python run_reports.py --output-dir ./out/2026-05
```

The `--client-id` value is the `customer_profile.id` from the API payload.

### Reporting periods

| `--period` | Window     | KPI P&L source                              |
| ---------- | ---------- | ------------------------------------------- |
| `daily`    | 1 day      | `performance_metrics.today_pnl`             |
| `weekly`   | 7 days     | `performance_metrics.weekly_pnl`            |
| `monthly`  | 30 days    | `performance_metrics.monthly_pnl` (default) |
| `3months`  | 90 days    | not exposed by API — KPI shows `—`          |
| `all`      | lifetime   | `performance_metrics.overall_profit`        |

In every case the **orders log + orders chart** is filtered to the selected
window using `recent_orders[*].created_at`. Open positions are point-in-time
state, so they are shown as-is regardless of period. Output filenames include
the period (e.g. `all_clients_report_2026_05_weekly.pdf`) so different periods
co-exist on disk.

Outputs:

```
<OUTPUT_DIR>/
├── clients/
│   ├── 29_softholding.pdf
│   └── …
└── all_clients_report_2026_05.pdf      ← emailed
```

The merged PDF has one bookmark per client for fast navigation.

## What the dashboard shows

* **KPI row** (5 cards): Account Balance · Overall P&L · Monthly P&L · Floating
  P&L · Open Positions. Each KPI is colour-coded green/red by sign and large
  enough for older audiences (target users are 60+).
* **Charts**: net signed notional of recent orders by ticker; floating P&L by
  open position (horizontal).
* **Tables**: recent orders log (ticker, side, time, qty, price, notional,
  status); open position detail (ticker, qty, current price, market value,
  floating % and $).

Closed-trade entry/exit pairing is **not** rendered because the API does not
expose paired buy/sell records — only an order/activity stream. If a closed-
trade endpoint is added later, the renderer is structured to plug it in
without template changes.

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
├── dashboard_renderer.py      # Jinja2 + inline SVG charts
├── pdf_generator.py           # Playwright HTML→PDF
├── pdf_merger.py              # pypdf merge + bookmarks
├── email_sender.py            # SMTP delivery
└── templates/
    └── dashboard.html         # Self-contained dashboard
```

## Troubleshooting

**`playwright._impl._errors.Error: Executable doesn't exist`**
→ `python -m playwright install chromium`.

**Fonts look like Times New Roman in the PDF**
→ The renderer waits up to 8 s for Google Fonts. If your environment blocks
`fonts.googleapis.com`, host the fonts locally.

**SMTP authentication fails on Gmail**
→ Use an *App Password*; `SMTP_PORT=587`, `SMTP_USE_TLS=true`.

**One client failure aborts the run**
→ It shouldn't — mapping and render errors are caught per-client and reported
in the summary. Only a failure on the single `/all-clients-data` call (network
or HTTP error) aborts the run.

**Re-running for the same month duplicates files**
→ It does not. Per-client PDFs and the merged PDF are overwritten in place.

## Security notes

- `api_code`, SMTP password, and account numbers are never logged.
- All secrets come from `.env`; only `.env.example` is committed.
