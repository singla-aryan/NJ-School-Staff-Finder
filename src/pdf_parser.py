from __future__ import annotations

import html as html_module
import io
import re

from pypdf import PdfReader

from .email_extractor import extract_contacts_from_html
from .models import CrawlPage, GeneralContact, StaffContact
from .role_matcher import detect_role


def parse_pdf(
    content: bytes,
    url: str,
    selected_categories: list[str] | None = None,
) -> tuple[CrawlPage, list[StaffContact], list[GeneralContact], bool]:
    reader = PdfReader(io.BytesIO(content))
    chunks: list[str] = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            chunks.append("")
    text = "\n".join(chunks).strip()
    scanned = not bool(re.sub(r"\s+", "", text))
    title = str((reader.metadata or {}).get("/Title") or "PDF document")
    contacts: list[StaffContact] = []
    general: list[GeneralContact] = []
    if text:
        contacts, general = extract_contacts_from_html(
            f"<html><head><title>{html_module.escape(title)}</title></head>"
            f"<body><pre>{html_module.escape(text)}</pre></body></html>",
            url,
            title,
            selected_categories,
        )
        for contact in [*contacts, *general]:
            contact.extraction_method = "public PDF text"
    page = CrawlPage(
        url=url, title=title, text=text, content_type="application/pdf",
        relevant=bool(detect_role(text, selected_categories) or contacts or general),
        extraction_method="pypdf",
    )
    return page, contacts, general, scanned

