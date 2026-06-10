"""HTTP client for the GemAlgo Alpaca-open API.

Endpoint:
    GET {API_BASE_URL}/all-clients-data?api_code={API_KEY}
        -> { "status": true, "data": [ <client record>, ... ] }

A single call returns every client. We never log the api_code.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_FACTOR = 2  # waits 2s, 4s, 8s between retries


class APIError(RuntimeError):
    pass


class APIClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 180.0):
        self.base_url = (base_url or os.getenv("API_BASE_URL", "https://api.gemalgo.com/api/alpaca-open")).rstrip("/")
        self.api_key = api_key or os.getenv("API_KEY", "%$%^&)(#PS@123456@#@")
        if not self.base_url:
            raise APIError("API_BASE_URL is not set")
        if not self.api_key:
            raise APIError("API_KEY is not set")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "ApexReports/1.0",
        })
        # Automatic retries on connection and 5xx errors
        retry_strategy = Retry(
            total=_MAX_RETRIES,
            backoff_factor=_BACKOFF_FACTOR,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def fetch_portfolio_history(
        self, account_number: str, start: str, end: str
    ) -> dict | None:
        """Best-effort historical snapshot for a single client account.

        Tries `GET {base}/portfolio-history?api_code=…&account=…&start=YYYY-MM-DD&end=YYYY-MM-DD`.
        Returns the parsed `data` payload on success, or `None` if the endpoint
        is missing / errored — callers must handle the `None` case.

        Expected payload (flexible — we read whatever fields are present):
            {
              "equity_at_end":   <float>,   # account equity at `end`
              "balance_at_end":  <float>,   # cash + positions value at `end`
              "period_pnl":      <float>,   # realized + unrealized P&L over window
              "period_pnl_pct":  <float>,
              "realized_pnl":    <float>,
              "unrealized_pnl":  <float>,
              "deposits":        <float>,
              "withdrawals":     <float>,
            }
        """
        url = f"{self.base_url}/portfolio-history"
        try:
            resp = self._session.get(
                url,
                params={
                    "api_code": self.api_key,
                    "account": account_number,
                    "start": start,
                    "end": end,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            log.warning("portfolio-history for %s failed: %s", account_number, exc)
            return None
        if resp.status_code == 404:
            log.info("portfolio-history endpoint not available (404)")
            return None
        if resp.status_code >= 400:
            log.warning("portfolio-history for %s returned HTTP %d", account_number, resp.status_code)
            return None
        try:
            body = resp.json()
        except ValueError:
            log.warning("portfolio-history for %s returned non-JSON", account_number)
            return None
        if isinstance(body, dict) and body.get("status") and isinstance(body.get("data"), dict):
            return body["data"]
        return None

    def fetch_all_clients(self) -> list[dict]:
        url = f"{self.base_url}/all-clients-data"
        log.debug("GET /all-clients-data")
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._session.get(url, params={"api_code": self.api_key}, timeout=self.timeout)
                break  # success
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    wait = _BACKOFF_FACTOR ** attempt
                    log.warning("attempt %d/%d failed (%s), retrying in %ds…", attempt, _MAX_RETRIES, exc, wait)
                    time.sleep(wait)
                else:
                    raise APIError(f"network error after {_MAX_RETRIES} attempts: {exc}") from exc
        if resp.status_code >= 400:
            raise APIError(f"all-clients-data returned HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise APIError("response was not valid JSON") from exc
        if not isinstance(body, dict) or not body.get("status"):
            raise APIError(f"API reported failure: {body!r}"[:200])
        data = body.get("data")
        if not isinstance(data, list):
            raise APIError("`data` field was not a list")
        return data


def _client_id_of(rec: dict) -> str:
    cp = rec.get("customer_profile") or {}
    return str(cp.get("id") or cp.get("username") or "?")


def fetch_all(client_filter: str | None = None) -> tuple[list[dict], list[tuple[str, str]]]:
    """Return (records, skipped). `records` are raw API records.

    Per-record validation errors are reported in `skipped`, never raised.
    A network/auth failure on the single endpoint *does* raise APIError —
    in that case the run cannot proceed at all.
    """
    api = APIClient()
    raw = api.fetch_all_clients()

    records: list[dict] = []
    skipped: list[tuple[str, str]] = []
    for rec in raw:
        if not isinstance(rec, dict):
            skipped.append(("?", "record was not an object"))
            continue
        cid = _client_id_of(rec)
        if client_filter and cid != str(client_filter):
            continue
        # Minimum required nesting; everything else is best-effort downstream.
        if "performance_metrics" not in rec or "financial_summary" not in rec:
            skipped.append((cid, "missing performance_metrics or financial_summary"))
            continue
        records.append(rec)

    if client_filter and not records:
        log.warning("no client matched --client-id=%s", client_filter)
    return records, skipped
