"""Render dashboard HTML → A4-landscape PDF using headless Chromium (Playwright)."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright, Browser

log = logging.getLogger(__name__)


@contextmanager
def browser_session():
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--font-render-hinting=none"])
        try:
            yield browser
        finally:
            browser.close()


def render_pdf(browser: Browser, html: str, out_path: Path, *, screenshot_path: Path | None = None) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(viewport={"width": 1480, "height": 1040})
    page = context.new_page()
    try:
        page.set_content(html, wait_until="networkidle")
        try:
            page.wait_for_function("document.fonts && document.fonts.status === 'loaded'", timeout=8000)
        except Exception:  # noqa: BLE001
            log.debug("font readiness wait timed out; continuing")
        page.evaluate("document.body.classList.add('pdf-mode')")

        if screenshot_path is not None:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_path), full_page=True)

        page.pdf(
            path=str(out_path),
            format="A4",
            landscape=False,
            print_background=True,
            prefer_css_page_size=True,
            margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"},
        )
    finally:
        context.close()
    return out_path
