"""Render dashboard HTML from a mapped (client, performance) view-model.

Inline SVG charts:
  * Left  — recent orders notional by ticker (buys green / sells red, aggregated)
  * Right — open positions floating $ by ticker (horizontal)
"""
from __future__ import annotations

import html
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


def fmt_k(v: float | int) -> str:
    if abs(v) >= 1000:
        return f"{'+' if v > 0 else '-' if v < 0 else ''}${abs(v)/1000:.1f}k"
    return f"{'+' if v > 0 else '-' if v < 0 else ''}${abs(v):,.0f}"


# ---------------- SVG charts ----------------

def _empty_svg(width: int, height: int, msg: str) -> str:
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="none">'
        f'<text x="{width/2}" y="{height/2}" text-anchor="middle" fill="#888" font-size="11">{html.escape(msg)}</text>'
        f'</svg>'
    )


def _svg_open_positions(positions: list[dict], width: int = 720, height: int = 280) -> str:
    if not positions:
        return _empty_svg(width, height, "No open positions")
    items = sorted(positions, key=lambda p: p["floating_dollar"])
    # size pad_l to longest ticker label and pad_r to the widest "$value" right-side label.
    max_ticker_len = max((len(p["ticker"]) for p in items), default=4)
    max_right_label = max(
        (len(fmt_money_signed(p["floating_dollar"])) for p in items),
        default=10,
    )
    pad_l = max(36, max_ticker_len * 7 + 14)
    pad_r = max(54, max_right_label * 6 + 12)
    pad_t, pad_b = 14, 32
    iw = width - pad_l - pad_r
    ih = height - pad_t - pad_b
    n = len(items)
    row_h = ih / n
    bar_h = row_h * 0.74
    hi = max((p["floating_dollar"] for p in items), default=0.0); hi = max(hi, 0.0)
    lo = min((p["floating_dollar"] for p in items), default=0.0); lo = min(lo, 0.0)
    span = (hi - lo) or 1.0
    # fonts shrink when rows get short so neighbouring labels don't overlap vertically.
    right_label_font = max(7.0, min(12.0, bar_h * 0.85))
    ticker_font = max(7.0, min(11.5, bar_h * 0.85))

    def x_of(v: float) -> float:
        return pad_l + iw * (v - lo) / span
    zero_x = x_of(0)

    grid = []
    for i in range(5):
        gv = lo + span * i / 4
        gx = x_of(gv)
        grid.append(f'<line class="grid-line" x1="{gx}" x2="{gx}" y1="{pad_t}" y2="{pad_t+ih}"/>')
        grid.append(f'<text class="axis-label" x="{gx}" y="{pad_t+ih+14}" text-anchor="middle">{fmt_k(gv)}</text>')

    bars, y_labels, vals = [], [], []
    for i, p in enumerate(items):
        y = pad_t + i * row_h + (row_h - bar_h)/2
        v = p["floating_dollar"]
        x = zero_x if v >= 0 else x_of(v)
        w = abs(x_of(v) - zero_x)
        cls = "bar-up" if v >= 0 else "bar-down"
        bars.append(f'<rect class="{cls}" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{bar_h:.1f}" rx="2"/>')
        y_labels.append(
            f'<text class="axis-label" x="{pad_l-8}" y="{y+bar_h/2+3:.1f}" text-anchor="end" '
            f'font-weight="600" font-size="{ticker_font:.1f}">{html.escape(p["ticker"])}</text>'
        )
        vals.append(
            f'<text class="bar-label" x="{pad_l+iw+6:.1f}" y="{y+bar_h/2+3:.1f}" '
            f'text-anchor="start" font-size="{right_label_font:.1f}">{fmt_money_signed(v)}</text>'
        )

    return f'''<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="none">
      {''.join(grid)}
      <line class="axis-line" x1="{zero_x}" x2="{zero_x}" y1="{pad_t}" y2="{pad_t+ih}"/>
      {''.join(bars)}{''.join(y_labels)}{''.join(vals)}
    </svg>'''


# ---------------- main renderer ----------------

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def render(client: dict, performance: dict, *, theme: str = "purple", page_num: int = 1) -> str:
    orders = performance.get("recent_orders", []) or []
    positions = performance.get("open_positions", []) or []
    buy_count = sum(1 for o in orders if o.get("side") == "buy")
    sell_count = sum(1 for o in orders if o.get("side") == "sell")
    orders_notional_total = sum(o.get("notional", 0.0) for o in orders)
    open_gaining = sum(1 for p in positions if p["floating_dollar"] > 0)
    open_losing = sum(1 for p in positions if p["floating_dollar"] < 0)

    ctx: dict[str, Any] = {
        "client": client,
        "perf": performance,
        "theme": theme if theme in ("purple", "yellow") else "purple",
        "source_site": SOURCE_SITE,
        "page_num": page_num,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "orders_notional_total": orders_notional_total,
        "open_gaining": open_gaining,
        "open_losing": open_losing,
        "open_chart_svg": _svg_open_positions(positions),
        "fmt_money": fmt_money,
        "fmt_money_signed": fmt_money_signed,
        "fmt_pct_signed": fmt_pct_signed,
    }
    return _env.get_template("dashboard.html").render(**ctx)
