"""Transform raw GemAlgo records into the view-model the dashboard renders.

The view-model is *period-aware*: the orders list, KPI value, chart, and
labelling all reflect whichever reporting period was selected.

Supported periods:
    daily      — today_pnl                  · orders in last 1 day
    weekly     — weekly_pnl                 · orders in last 7 days
    monthly    — monthly_pnl                · orders in last 30 days   (default)
    3months    — (not exposed by API → —)   · orders in last 90 days
    all        — overall_profit (lifetime)  · all available orders

Output `client`:
    { client_id, name, email, strategy, risk_level, platform,
      account_number, is_paper }

Output `performance`:
    {
      report_period, period_label, period_days,
      report_month, generated_date,
      account_size, overall_profit, overall_roi_pct,
      period_pnl, period_pnl_pct,             # None when not exposed (3months)
      monthly_pnl, monthly_pnl_pct,
      weekly_pnl,  weekly_pnl_pct,
      today_pnl,   today_pnl_pct,
      floating_pnl, floating_pnl_pct,
      open_positions_count,
      equity, cash, buying_power, long_market_value, short_market_value,
      open_positions: [...],                  # current state — period-independent
      recent_orders:  [...],                  # filtered to the selected period
    }
"""
from __future__ import annotations

import datetime as dt
from typing import Any

# period key → (label, lookback_days_or_None)
PERIODS: dict[str, tuple[str, int | None]] = {
    "daily":   ("Daily",          1),
    "weekly":  ("Weekly",         7),
    "monthly": ("Monthly",        30),
    "3months": ("Last 3 Months",  90),
    "all":     ("All History",    None),
}


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return default if v is None else float(v)
    except (TypeError, ValueError):
        return default


def _int(v: Any, default: int = 0) -> int:
    try:
        return default if v is None else int(v)
    except (TypeError, ValueError):
        return default


def _parse_iso(s: str | None) -> dt.datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _client_view(rec: dict) -> dict:
    cp = rec.get("customer_profile") or {}
    am = rec.get("account_mapping") or {}
    strat = rec.get("strategy_info") or {}
    first = cp.get("first_name") or ""
    last = cp.get("last_name") or ""
    full = f"{first} {last}".strip()
    name = full or cp.get("username") or cp.get("email") or f"client-{cp.get('id', '?')}"
    return {
        "client_id": str(cp.get("id") or cp.get("username") or "unknown"),
        "name": name,
        "email": cp.get("email") or "",
        "strategy": "Orion",
        "risk_level": (strat.get("risk_level") or "").replace("_", " "),
        "platform": strat.get("platform") or "—",
        "account_number": am.get("account_number") or "—",
        "is_paper": bool(am.get("is_paper")),
    }


def _select_period_pnl(period: str, pm: dict, pct: dict) -> tuple[float | None, float | None]:
    """Return (period_pnl, period_pnl_pct). `None` for periods the API does not expose."""
    if period == "daily":
        return _num(pm.get("today_pnl")), _num(pct.get("daily"))
    if period == "weekly":
        return _num(pm.get("weekly_pnl")), _num(pct.get("weekly"))
    if period == "monthly":
        return _num(pm.get("monthly_pnl")), _num(pct.get("monthly"))
    if period == "all":
        return _num(pm.get("overall_pnl")), _num(pct.get("overall") or pm.get("overall_roi_pct"))
    # 3months — not exposed by API
    return None, None


def _filter_orders(orders_raw: list[dict], *, lookback_days: int | None, now: dt.datetime) -> list[dict]:
    out: list[dict] = []
    cutoff = None
    if lookback_days is not None:
        cutoff_dt = now - dt.timedelta(days=lookback_days)
        # The API returns timezone-aware UTC timestamps. Compare in UTC.
        cutoff = cutoff_dt.astimezone(dt.timezone.utc) if cutoff_dt.tzinfo else cutoff_dt.replace(tzinfo=dt.timezone.utc)

    for o in orders_raw:
        ts = _parse_iso(o.get("created_at"))
        if cutoff is not None:
            if ts is None:
                continue  # cannot place untimed orders inside a window
            ts_utc = ts.astimezone(dt.timezone.utc) if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)
            if ts_utc < cutoff:
                continue
        out.append({
            "id": str(o.get("id", ""))[:8],
            "ticker": o.get("symbol") or "—",
            "side": (o.get("side") or "").lower(),
            "qty": _int(o.get("qty")),
            "price": _num(o.get("filled_avg_price")),
            "notional": _num(o.get("filled_avg_price")) * _int(o.get("qty")),
            "status": o.get("status") or "—",
            "when": ts.strftime("%b %d %H:%M") if ts else (o.get("created_at") or "—"),
            "when_date": ts.strftime("%b %d") if ts else "—",
        })
    return out


def _performance_view(rec: dict, *, period: str, now: dt.datetime) -> dict:
    if period not in PERIODS:
        period = "monthly"
    label, days = PERIODS[period]

    pm = rec.get("performance_metrics") or {}
    pct = pm.get("pnl_percentages") or {}
    fs = rec.get("financial_summary") or {}
    em = fs.get("equity_metrics") or {}
    positions_raw = fs.get("current_positions") or []
    orders_raw = (rec.get("trading_history") or {}).get("recent_orders") or []

    account_size = _num(pm.get("balance") or pm.get("current_balance"))

    open_positions = []
    for p in positions_raw:
        floating = _num(p.get("unrealized_pl"))
        floating_pct = _num(p.get("unrealized_plpc")) * 100.0  # API gives ratio
        open_positions.append({
            "ticker": p.get("symbol") or "—",
            "qty": _int(p.get("qty")),
            "current_price": _num(p.get("current_price")),
            "market_value": _num(p.get("market_value")),
            "floating_pct": floating_pct,
            "floating_dollar": floating,
        })
    floating_total = sum(p["floating_dollar"] for p in open_positions)
    floating_total_pct = (floating_total / account_size * 100.0) if account_size else 0.0

    # Period-over-period equity growth (most recent close vs previous close).
    equity_val = _num(em.get("equity"))
    last_equity = _num(em.get("last_equity"))
    equity_growth_dollar = equity_val - last_equity
    equity_growth_pct = (equity_growth_dollar / last_equity * 100.0) if last_equity else 0.0

    recent_orders = _filter_orders(orders_raw, lookback_days=days, now=now)
    period_pnl, period_pnl_pct = _select_period_pnl(period, pm, pct)

    return {
        "report_period": period,
        "period_label": label,
        "period_days": days,
        "report_month": now.strftime("%B %Y"),
        "generated_date": now.strftime("%B %d, %Y"),
        "account_size": account_size,
        "overall_profit": _num(pm.get("overall_pnl")),
        "overall_roi_pct": _num(pct.get("overall") or pm.get("overall_roi_pct")),
        "period_pnl": period_pnl,
        "period_pnl_pct": period_pnl_pct,
        "monthly_pnl": _num(pm.get("monthly_pnl")),
        "monthly_pnl_pct": _num(pct.get("monthly")),
        "weekly_pnl": _num(pm.get("weekly_pnl")),
        "weekly_pnl_pct": _num(pct.get("weekly")),
        "today_pnl": _num(pm.get("today_pnl")),
        "today_pnl_pct": _num(pct.get("daily")),
        "floating_pnl": floating_total,
        "floating_pnl_pct": floating_total_pct,
        "equity_growth_dollar": equity_growth_dollar,
        "equity_growth_pct": equity_growth_pct,
        "last_equity": last_equity,
        "open_positions_count": len(open_positions),
        "equity": _num(em.get("equity")),
        "cash": _num(em.get("cash")),
        "buying_power": _num(em.get("buying_power")),
        "long_market_value": _num(em.get("long_market_value")),
        "short_market_value": _num(em.get("short_market_value")),
        "open_positions": open_positions,
        "recent_orders": recent_orders,
    }


def map_record(rec: dict, *, now: dt.datetime | None = None, period: str = "monthly") -> tuple[dict, dict]:
    return _client_view(rec), _performance_view(rec, period=period, now=now or dt.datetime.now())
