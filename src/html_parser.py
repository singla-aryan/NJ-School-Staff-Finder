from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .models import CrawlPage, GeneralContact, StaffContact
from .email_extractor import extract_contacts_from_html
from .role_matcher import detect_role
from .utilities import normalize_url

SUPPORT_TOPIC_TERMS = (
    "counseling", "counselor", "guidance", "student-services", "studentservices",
    "support-services", "studentsupport", "psychology", "psychologist", "social-work",
    "socialworker", "mental-health", "behavioral-health", "wellness", "child-study",
    "pupil-services", "special-services", "intervention", "student-assistance",
    "case-manager", "learning-consultant", "therapist", "therapy", "clinician",
    "clinical-counselor", "lcsw", "lpc", "lac", "substance-use", "substance-awareness",
    "prevention-counselor", "crisis-counselor", "crisis-intervention",
    "psychological-services", "behavior-specialist",
)
DIRECTORY_TERMS = (
    "staff", "directory", "faculty", "contacts", "departments",
)
PRIORITY_TERMS = SUPPORT_TOPIC_TERMS + DIRECTORY_TERMS
BRIDGE_TERMS = (
    "academics", "student-services", "student services", "support", "services",
    "departments", "programs", "school-information", "about-us", "our-school",
)
LOW_VALUE_TERMS = (
    "/athletics", "/sports", "/calendar", "/events", "/news", "/board", "/boe",
    "/employment", "/jobs", "/menus", "/lunch", "/food-service", "/transportation",
    "/registration", "/assignments", "/summer", "/curriculum", "/policies", "/policy/",
    "/minutes", "/agendas", "/facilities", "/budget", "/bids", "/podcast",
)
SKIP_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".mp4", ".mp3", ".zip",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".ics", ".css", ".js",
)


@dataclass(slots=True)
class ParsedHTML:
    page: CrawlPage
    links: list[str]
    priority_links: set[str]
    support_links: set[str]
    directory_links: set[str]
    role_links: set[str]
    contacts: list[StaffContact]
    general_contacts: list[GeneralContact]


def is_priority_url(url: str) -> bool:
    lowered = url.casefold()
    return any(term in lowered for term in PRIORITY_TERMS)


def is_support_topic_url(url: str) -> bool:
    lowered = url.casefold()
    return any(term in lowered for term in SUPPORT_TOPIC_TERMS)


def is_directory_url(url: str) -> bool:
    lowered = url.casefold()
    return any(term in lowered for term in DIRECTORY_TERMS)


def is_bridge_url(url: str) -> bool:
    lowered = url.casefold()
    return any(term in lowered for term in BRIDGE_TERMS)


def is_low_value_url(url: str) -> bool:
    lowered = url.casefold()
    return any(term in lowered for term in LOW_VALUE_TERMS)


def is_pdf_url(url: str) -> bool:
    return urlparse(url).path.casefold().endswith(".pdf")


def _nearby_link_context(anchor: Tag) -> str:
    """Return text from a small card/row around a link, never the whole page."""
    current = anchor.parent
    for _ in range(4):
        if not isinstance(current, Tag) or current.name in {"body", "html"}:
            break
        text = current.get_text(" ", strip=True)
        link_count = len(current.select("a[href]"))
        if (
            current.name in {"li", "tr", "article", "section", "div", "dd", "p"}
            and len(text) <= 500
            and link_count <= 3
        ):
            return text
        current = current.parent
    return ""


def parse_html_page(
    html: str,
    url: str,
    depth: int = 0,
    method: str = "httpx",
    selected_categories: list[str] | None = None,
) -> ParsedHTML:
    soup = BeautifulSoup(html or "", "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for unwanted in soup(["style", "noscript", "template"]):
        unwanted.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    contacts, general = extract_contacts_from_html(html, url, title, selected_categories)
    links: list[str] = []
    priority_links: set[str] = set()
    support_links: set[str] = set()
    directory_links: set[str] = set()
    role_links: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        target = normalize_url(href, url)
        if not target or urlparse(target).path.casefold().endswith(SKIP_EXTENSIONS):
            continue
        links.append(target)
        anchor_text = anchor.get_text(" ", strip=True)
        nearby_text = _nearby_link_context(anchor)
        direct_context = f"{target} {anchor_text}"
        link_context = f"{direct_context} {nearby_text}"
        role_match = detect_role(nearby_text, selected_categories)
        if role_match is not None:
            role_links.add(target)
        if is_support_topic_url(link_context):
            support_links.add(target)
        if is_directory_url(direct_context):
            directory_links.add(target)
        if target in support_links or target in directory_links or role_match is not None:
            priority_links.add(target)
    for meta in soup.select("meta[http-equiv]"):
        if meta.get("http-equiv", "").casefold() != "refresh":
            continue
        match = re.search(r"url\s*=\s*['\"]?([^'\";]+)", meta.get("content", ""), re.I)
        target = normalize_url(match.group(1).strip(), url) if match else ""
        if target:
            links.append(target)
            priority_links.add(target)
            support_links.add(target)
    links = list(dict.fromkeys(links))
    links.sort(key=lambda value: (not is_priority_url(value), len(value)))
    relevant = is_priority_url(url) or detect_role(text, selected_categories) is not None or bool(contacts or general)
    page = CrawlPage(
        url=url, title=title, text=text, html=html, depth=depth,
        extraction_method=method, relevant=relevant,
    )
    return ParsedHTML(
        page=page,
        links=links,
        priority_links=priority_links,
        support_links=support_links,
        directory_links=directory_links,
        role_links=role_links,
        contacts=contacts,
        general_contacts=general,
    )


def sitemap_links(xml: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(xml or "", "xml")
    return [normalize_url(node.get_text(strip=True), base_url) for node in soup.find_all("loc") if node.get_text(strip=True)]
