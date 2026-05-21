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

import requests

log = logging.getLogger(__name__)


class APIError(RuntimeError):
    pass


class APIClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 60.0):
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

    def fetch_all_clients(self) -> list[dict]:
        url = f"{self.base_url}/all-clients-data"
        log.debug("GET /all-clients-data")
        try:
            resp = self._session.get(url, params={"api_code": self.api_key}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise APIError(f"network error: {exc}") from exc
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
