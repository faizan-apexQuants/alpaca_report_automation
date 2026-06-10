"""Render dashboard HTML from a mapped (client, performance) view-model."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).parent / "templates"
SOURCE_SITE = "gemalgo.com"


# ---------------- number formatting ----------------

def fmt_money(v: float | int) -> str:
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def fmt_money_signed(v: float | int) -> str:
    sign = "+" if v > 0 else ("-" if v < 0 else "")
    return f"{sign}${abs(v):,.2f}"


def fmt_pct_signed(v: float | int) -> str:
    sign = "+" if v > 0 else ("-" if v < 0 else "")
    return f"{sign}{abs(v):.3f}%"


# ---------------- main renderer ----------------

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


# Rows per closed-trades page in the PDF. Sized so the table fits the A4-portrait
# body after the header / tagbar / card-head / footer overhead. Adjust if the
# table font or row padding changes.
TRADES_PER_PAGE = 28


def render(client: dict, performance: dict, *, theme: str = "purple", page_num: int = 1) -> str:
    closed_trades = performance.get("closed_trades", []) or []
    closed_trades_pnl = sum(t.get("pnl", 0.0) for t in closed_trades)
    closed_trades_notional = sum(t.get("notional", 0.0) for t in closed_trades)

    # Paginate. Always emit at least one trades page (empty-state placeholder).
    if closed_trades:
        trade_chunks = [
            closed_trades[i:i + TRADES_PER_PAGE]
            for i in range(0, len(closed_trades), TRADES_PER_PAGE)
        ]
    else:
        trade_chunks = [[]]
    total_pages = 1 + len(trade_chunks)

    ctx: dict[str, Any] = {
        "client": client,
        "perf": performance,
        "theme": theme if theme in ("purple", "yellow") else "purple",
        "source_site": SOURCE_SITE,
        "page_num": page_num,
        "closed_trades_pnl": closed_trades_pnl,
        "closed_trades_notional": closed_trades_notional,
        "trade_chunks": trade_chunks,
        "total_pages": total_pages,
        "fmt_money": fmt_money,
        "fmt_money_signed": fmt_money_signed,
        "fmt_pct_signed": fmt_pct_signed,
    }
    return _env.get_template("dashboard.html").render(**ctx)
