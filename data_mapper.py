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


def _overall_pnl(pm: dict) -> tuple[float, float]:
    """Compute lifetime P&L and ROI %, deposit/withdrawal-safe.

        Net Capital     = Total Deposits − Total Withdrawals
        Overall P&L     = Current Equity − Net Capital
        Overall ROI %   = Overall P&L ÷ Net Capital × 100   (0 if net capital == 0)
    """
    current_equity = _num(pm.get("total_equity") or pm.get("balance") or pm.get("current_balance"))
    net_capital = _num(pm.get("total_deposits")) - _num(pm.get("total_withdrawals"))
    overall_pnl = current_equity - net_capital
    overall_roi_pct = (overall_pnl / net_capital * 100.0) if net_capital else 0.0
    return overall_pnl, overall_roi_pct


def _select_period_pnl(period: str, pm: dict, pct: dict) -> tuple[float | None, float | None]:
    """Return (period_pnl, period_pnl_pct). `None` for periods the API does not expose."""
    if period == "daily":
        return _num(pm.get("today_pnl")), _num(pct.get("daily"))
    if period == "weekly":
        return _num(pm.get("weekly_pnl")), _num(pct.get("weekly"))
    if period == "monthly":
        return _num(pm.get("monthly_pnl")), _num(pct.get("monthly"))
    if period == "all":
        return _overall_pnl(pm)
    # 3months — not exposed by API
    return None, None


def _to_utc(d: dt.datetime) -> dt.datetime:
    return d.astimezone(dt.timezone.utc) if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _filter_orders(
    orders_raw: list[dict],
    *,
    lookback_days: int | None,
    now: dt.datetime,
    window: tuple[dt.datetime, dt.datetime] | None = None,
) -> list[dict]:
    """Filter orders to a time window.

    * `window` (start, end) takes precedence — used for explicit historical months.
    * Otherwise falls back to `lookback_days` ending at `now`.
    """
    out: list[dict] = []
    cutoff_lo: dt.datetime | None = None
    cutoff_hi: dt.datetime | None = None
    if window is not None:
        cutoff_lo = _to_utc(window[0])
        cutoff_hi = _to_utc(window[1])
    elif lookback_days is not None:
        cutoff_lo = _to_utc(now - dt.timedelta(days=lookback_days))

    for o in orders_raw:
        ts = _parse_iso(o.get("created_at"))
        if cutoff_lo is not None or cutoff_hi is not None:
            if ts is None:
                continue  # cannot place untimed orders inside a window
            ts_utc = _to_utc(ts)
            if cutoff_lo is not None and ts_utc < cutoff_lo:
                continue
            if cutoff_hi is not None and ts_utc > cutoff_hi:
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


def _month_window(year: int, month: int) -> tuple[dt.datetime, dt.datetime]:
    """Return UTC (start_of_month, end_of_month_inclusive) datetimes."""
    start = dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
    if month == 12:
        next_start = dt.datetime(year + 1, 1, 1, tzinfo=dt.timezone.utc)
    else:
        next_start = dt.datetime(year, month + 1, 1, tzinfo=dt.timezone.utc)
    end = next_start - dt.timedelta(microseconds=1)
    return start, end


def _reconstruct_month_pnl_from_orders(orders_raw: list[dict], year: int, month: int) -> float:
    """Sells − Buys notional for the calendar month (UTC). Same proxy as the
    monthly-returns chart — not true realized P&L but useful when no historical
    snapshot is available."""
    total = 0.0
    for o in orders_raw:
        ts = _parse_iso(o.get("created_at"))
        if ts is None:
            continue
        ts_utc = _to_utc(ts)
        if ts_utc.year != year or ts_utc.month != month:
            continue
        notional = _num(o.get("filled_avg_price")) * _int(o.get("qty"))
        sign = 1.0 if (o.get("side") or "").lower() == "sell" else -1.0
        total += sign * notional
    return total


def _performance_view(
    rec: dict,
    *,
    period: str,
    now: dt.datetime,
    month: tuple[int, int] | None = None,
) -> dict:
    if period not in PERIODS:
        period = "monthly"
    label, days = PERIODS[period]
    # When a historical month is requested, scope the orders to that calendar month
    # and re-label everything accordingly. KPIs still reflect current account state.
    month_window: tuple[dt.datetime, dt.datetime] | None = None
    if month is not None and period == "monthly":
        month_window = _month_window(*month)
        days = None  # disable lookback-days; we'll use the explicit window
        label_dt = dt.datetime(month[0], month[1], 1)
        report_month_str = label_dt.strftime("%B %Y")
    else:
        report_month_str = now.strftime("%B %Y")

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

    # Growth = realized return on the capital the client has actually committed.
    #   Net Capital = Total Deposits − Total Withdrawals
    #   Growth %    = Realized P&L ÷ Net Capital × 100
    equity_val = _num(em.get("equity"))
    last_equity = _num(em.get("last_equity"))
    realized_pnl = _num(pm.get("realized_pnl"))
    net_capital = _num(pm.get("total_deposits")) - _num(pm.get("total_withdrawals"))
    equity_growth_dollar = realized_pnl
    equity_growth_pct = (realized_pnl / net_capital * 100.0) if net_capital else 0.0

    recent_orders = _filter_orders(orders_raw, lookback_days=days, now=now, window=month_window)
    period_pnl, period_pnl_pct = _select_period_pnl(period, pm, pct)

    # ---- Historical view overrides --------------------------------------------------
    # When a past month was requested, prefer a real point-in-time snapshot from the
    # GemAlgo `/portfolio-history` endpoint (attached upstream as `_historical_snapshot`).
    # If the snapshot isn't available, reconstruct period P&L from the order log so at
    # least the period KPI matches the trade activity shown below it.
    is_historical = False
    snapshot_source = None
    if month is not None and period == "monthly":
        is_historical = True
        snap = rec.get("_historical_snapshot") or None
        if isinstance(snap, dict):
            snapshot_source = "api"
            if snap.get("equity_at_end") is not None:
                equity_val = _num(snap.get("equity_at_end"))
            if snap.get("balance_at_end") is not None:
                account_size = _num(snap.get("balance_at_end"))
            if snap.get("realized_pnl") is not None:
                realized_pnl = _num(snap.get("realized_pnl"))
            if snap.get("unrealized_pnl") is not None:
                floating_total = _num(snap.get("unrealized_pnl"))
                floating_total_pct = (floating_total / account_size * 100.0) if account_size else 0.0
            if snap.get("period_pnl") is not None:
                period_pnl = _num(snap.get("period_pnl"))
                period_pnl_pct = _num(snap.get("period_pnl_pct"))
            if snap.get("deposits") is not None or snap.get("withdrawals") is not None:
                net_capital = _num(snap.get("deposits")) - _num(snap.get("withdrawals"))
            # recompute growth from the historical realized P&L + net capital
            equity_growth_dollar = realized_pnl
            equity_growth_pct = (realized_pnl / net_capital * 100.0) if net_capital else 0.0
        else:
            snapshot_source = "reconstructed"
            # Fall back: scope period_pnl to the requested month using the orders proxy.
            period_pnl = _reconstruct_month_pnl_from_orders(orders_raw, *month)
            period_pnl_pct = (period_pnl / account_size * 100.0) if account_size else 0.0

    return {
        "report_period": period,
        "period_label": label,
        "period_days": days,
        "report_month": report_month_str,
        "generated_date": now.strftime("%B %d, %Y"),
        "account_size": account_size,
        "overall_profit": _overall_pnl(pm)[0],
        "overall_roi_pct": _overall_pnl(pm)[1],
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
        "is_historical": is_historical,
        "snapshot_source": snapshot_source,
        "equity": _num(em.get("equity")),
        "cash": _num(em.get("cash")),
        "buying_power": _num(em.get("buying_power")),
        "long_market_value": _num(em.get("long_market_value")),
        "short_market_value": _num(em.get("short_market_value")),
        "open_positions": open_positions,
        "recent_orders": recent_orders,
    }


def map_record(
    rec: dict,
    *,
    now: dt.datetime | None = None,
    period: str = "monthly",
    month: tuple[int, int] | None = None,
) -> tuple[dict, dict]:
    return _client_view(rec), _performance_view(
        rec, period=period, now=now or dt.datetime.now(), month=month
    )
