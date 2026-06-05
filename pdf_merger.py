"""Merge per-client PDFs into one master PDF with bookmarks."""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from pypdf import PdfReader, PdfWriter

log = logging.getLogger(__name__)


def merge_pdfs(entries: list[tuple[str, Path]], output_path: Path) -> Path:
    writer = PdfWriter()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    for title, pdf_path in entries:
        if not pdf_path.exists():
            log.warning("merge: skipping missing pdf %s", pdf_path)
            continue
        try:
            reader = PdfReader(str(pdf_path))
        except Exception as exc:  # noqa: BLE001
            log.error("merge: failed to read %s: %s", pdf_path, exc)
            continue
        start = len(writer.pages)
        for p in reader.pages:
            writer.add_page(p)
        try:
            writer.add_outline_item(title, start)
        except Exception as exc:  # noqa: BLE001
            log.debug("merge: bookmark add failed for %s: %s", title, exc)

    if output_path.exists():
        try:
            output_path.unlink()
        except PermissionError:
            # File is locked (e.g. open in a PDF viewer) — write to a
            # timestamped fallback so the run doesn't fail.
            stamp = dt.datetime.now().strftime("%H%M%S")
            fallback = output_path.with_stem(f"{output_path.stem}_{stamp}")
            log.warning(
                "merge: %s is locked by another process, writing to %s instead",
                output_path.name, fallback.name,
            )
            output_path = fallback
    with output_path.open("wb") as fh:
        writer.write(fh)
    return output_path
