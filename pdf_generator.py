"""Render dashboard HTML → A4-landscape PDF using headless Chromium (Playwright)."""
from __future__ import annotations

import logging
import shutil
import tempfile
import time
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


def _safe_write_pdf(page, out_path: Path, pdf_kwargs: dict) -> None:
    """Write PDF with retry when the target file is locked (e.g. OneDrive sync).

    Strategy:
      1. Try writing directly.
      2. On PermissionError, delete the old file, wait briefly, and retry.
      3. If still blocked, write to a temp file in the same dir and move it.
    """
    for attempt in range(1, 4):
        try:
            page.pdf(path=str(out_path), **pdf_kwargs)
            return
        except PermissionError:
            if attempt == 1 and out_path.exists():
                log.warning("PDF locked, deleting old file and retrying: %s", out_path.name)
                try:
                    out_path.unlink()
                except PermissionError:
                    pass  # will try temp-file fallback next
                time.sleep(0.5)
            elif attempt == 2:
                log.warning("PDF still locked, writing to temp file: %s", out_path.name)
                fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=str(out_path.parent))
                try:
                    import os; os.close(fd)
                    page.pdf(path=tmp, **pdf_kwargs)
                    shutil.move(tmp, str(out_path))
                    return
                except Exception:
                    Path(tmp).unlink(missing_ok=True)
                    raise
            else:
                raise


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

        _safe_write_pdf(page, out_path, {
            "format": "A4",
            "landscape": False,
            "print_background": True,
            "prefer_css_page_size": True,
            "margin": {"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"},
        })
    finally:
        context.close()
    return out_path
