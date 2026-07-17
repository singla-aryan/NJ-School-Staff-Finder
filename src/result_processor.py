from __future__ import annotations

import html as html_module
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from urllib.parse import urlparse

from .crawler import CrawlOutcome, SiteCrawler
from .html_parser import is_priority_url
from .email_extractor import extract_contacts_from_html
from .models import (
    GeneralContact,
    ProgressCallback,
    SchoolMatch,
    SchoolSearchResult,
    SearchSettings,
    StaffContact,
    WebsiteMatch,
)
from .role_matcher import REVIEW_CATEGORY, ROLE_CATEGORIES, detect_role, role_sort_key
from .utilities import dedupe_preserve_order, same_domain, utc_now
from .website_finder import WebsiteFinder

MAX_CONCURRENT_SCHOOLS = 3


def _name_sort_parts(name: str) -> tuple[str, str]:
    parts = [part for part in name.casefold().split() if part]
    return (parts[-1] if parts else "", parts[0] if parts else "")


def _contact_strength(contact: StaffContact) -> tuple[int, int, int, int, int]:
    confidence = {"Verified": 3, "Strong": 2, "Needs Review": 1}.get(contact.email_confidence, 0)
    named = int(bool(contact.staff_name and contact.staff_name != "Name not clearly stated"))
    role = detect_role(contact.role)
    category_strength = len(ROLE_CATEGORIES) - role_sort_key(contact.role_category)
    return confidence, named, category_strength, int(bool(contact.credentials)), role.score if role else len(contact.role)


def deduplicate_contacts(contacts: list[StaffContact]) -> list[StaffContact]:
    grouped: dict[str, StaffContact] = {}
    for contact in contacts:
        key = contact.email.casefold()
        if key not in grouped:
            grouped[key] = replace(contact, source_urls=list(contact.source_urls))
            continue
        current = grouped[key]
        sources = dedupe_preserve_order([*current.source_urls, *contact.source_urls])
        winner = contact if _contact_strength(contact) > _contact_strength(current) else current
        credentials = dedupe_preserve_order([
            part.strip()
            for value in (current.credentials, contact.credentials)
            for part in value.split(",")
            if part.strip()
        ])
        grouped[key] = replace(
            winner,
            source_urls=sources,
            source_url=sources[0],
            credentials=", ".join(credentials),
        )
    return sorted(
        grouped.values(),
        key=lambda item: (role_sort_key(item.role_category), *_name_sort_parts(item.staff_name), item.email),
    )


def deduplicate_general_contacts(contacts: list[GeneralContact]) -> list[GeneralContact]:
    grouped: dict[str, GeneralContact] = {}
    for contact in contacts:
        key = contact.email.casefold()
        if key not in grouped:
            grouped[key] = replace(contact, source_urls=list(contact.source_urls))
        else:
            current = grouped[key]
            sources = dedupe_preserve_order([*current.source_urls, *contact.source_urls])
            chosen = contact if len(contact.department_name) > len(current.department_name) else current
            grouped[key] = replace(chosen, source_urls=sources, source_url=sources[0])
    return sorted(grouped.values(), key=lambda item: (role_sort_key(item.role_category), item.department_name.casefold(), item.email))


def _is_hib_source(url: str) -> bool:
    lowered = url.casefold()
    return any(marker in lowered for marker in ("/hib", "anti-bullying", "anti_bullying"))


def _prefer_direct_staff_sources(contacts: list[StaffContact]) -> list[StaffContact]:
    """Use a dedicated roster/profile over a noisier district-wide HIB duplicate."""
    directly_sourced = {
        contact.email.casefold()
        for contact in contacts
        if not _is_hib_source(contact.source_url)
    }
    return [
        contact for contact in contacts
        if not _is_hib_source(contact.source_url) or contact.email.casefold() not in directly_sourced
    ]


def _school_label_from_source(url: str) -> str:
    for segment in (part for part in urlparse(url).path.split("/") if part):
        lowered = segment.casefold()
        if lowered.endswith(("-school", "_school", "-academy", "_academy")):
            return re.sub(r"[-_]+", " ", segment).title()
    return ""


def _contact_school_label(contact: StaffContact, default_school: str) -> str:
    evidence = html_module.unescape(contact.evidence_snippet)
    name_position = evidence.casefold().find(contact.staff_name.casefold())
    if name_position >= 0:
        school_matches = list(re.finditer(
            r"\b((?:[A-Z][A-Za-z&.'’()-]*\s+){1,6}(?:School|Academy))\b",
            evidence[:name_position],
        ))
        if school_matches:
            label = re.sub(r"^(?:At|The)\s+", "", school_matches[-1].group(1)).strip()
            if label:
                return label
    return _school_label_from_source(contact.source_url) or default_school


def process_crawl_outcome(result: SchoolSearchResult, outcome: CrawlOutcome, settings: SearchSettings) -> SchoolSearchResult:
    school_name = result.school.canonical_name if result.school else result.input_school_name
    district_name = result.school.district_name if result.school else ""
    contacts = deduplicate_contacts([
        replace(
            contact,
            school=_contact_school_label(contact, school_name),
            district=district_name,
        )
        for contact in _prefer_direct_staff_sources(outcome.contacts)
    ])
    named_emails = {contact.email.casefold() for contact in contacts if contact.staff_name != "Name not clearly stated"}
    general = [contact for contact in deduplicate_general_contacts(outcome.general_contacts) if contact.email.casefold() not in named_emails]
    result.contacts = [
        contact for contact in contacts
        if contact.email_confidence in {"Verified", "Strong"} and contact.role_category != REVIEW_CATEGORY
    ]
    result.review_contacts = [
        contact for contact in contacts
        if contact.email_confidence == "Needs Review" or contact.role_category == REVIEW_CATEGORY
    ]
    result.general_contacts = general if settings.include_general_contacts else []
    result.pages_searched = len(outcome.pages)
    result.total_pages_discovered = max(outcome.total_pages_discovered, result.pages_searched)
    result.relevant_pages = outcome.relevant_pages
    result.pdfs_inspected = outcome.pdfs_inspected
    result.scanned_pdfs = outcome.scanned_pdfs
    result.javascript_used = outcome.javascript_used
    result.crawl_restricted = outcome.restricted
    result.page_limit_reached = outcome.page_limit_reached
    result.errors.extend(outcome.errors)
    result.checked_at = utc_now()
    if result.contacts or result.general_contacts:
        result.status = "Completed — contacts found"
    elif result.review_contacts:
        result.status = "Completed - possible contacts need review"
    elif outcome.restricted and not outcome.pages:
        result.status = "Website blocked crawling"
    elif outcome.errors and outcome.pages:
        result.status = "Partial result"
    elif outcome.errors and not outcome.pages:
        result.status = "Failed with recoverable error"
    else:
        result.status = "Completed — no public contacts found"
    return result


class SchoolSearchService:
    """Coordinates discovery and crawl while keeping each school failure isolated."""

    def __init__(self, settings: SearchSettings, progress: ProgressCallback | None = None):
        self.settings = settings
        self.progress = progress or (lambda _stage, _data: None)

    def search_match(
        self,
        match: SchoolMatch,
        website_match: WebsiteMatch | None = None,
        broad: bool = False,
    ) -> SchoolSearchResult:
        result = SchoolSearchResult(input_school_name=match.input_school.raw_name, school_match=match, checked_at=utc_now())
        if match.status in {"Needs Review", "Not Found"} or not match.candidate:
            result.status = "School match needs review"
            return result
        self.progress("identifying", {"status": match.status, "school": match.candidate.canonical_name})
        if website_match is None:
            finder = WebsiteFinder(timeout=self.settings.timeout)
            website_match = finder.find(match.candidate, broad=broad)
            result.discovery_attempts = finder.attempts
        result.website_match = website_match
        self.progress("website", {"status": website_match.status, "url": website_match.url})
        if website_match.status == "Needs Confirmation":
            result.status = "Website needs confirmation"
            return result
        if website_match.status == "Not Found" or not website_match.url:
            result.status = "Website not found"
            return result
        seed_urls = [
            candidate.url
            for candidate in website_match.candidates
            if same_domain(website_match.url, candidate.url)
            and is_priority_url(f"{candidate.url} {candidate.title} {candidate.snippet}")
        ]
        outcome = SiteCrawler(self.settings, self.progress).crawl(website_match.url, seed_urls=seed_urls)
        return process_crawl_outcome(result, outcome, self.settings)

    def process_batch(self, matches: list[SchoolMatch]) -> list[SchoolSearchResult]:
        if not matches:
            return []
        results: list[SchoolSearchResult | None] = [None] * len(matches)

        def search_one(match: SchoolMatch) -> SchoolSearchResult:
            try:
                return self.search_match(match)
            except Exception as exc:
                return SchoolSearchResult(
                    input_school_name=match.input_school.raw_name,
                    school_match=match,
                    status="Failed with recoverable error",
                    errors=[f"{type(exc).__name__}: {exc}"],
                    checked_at=utc_now(),
                )

        with ThreadPoolExecutor(
            max_workers=min(MAX_CONCURRENT_SCHOOLS, len(matches)),
            thread_name_prefix="school-search",
        ) as executor:
            futures = {
                executor.submit(search_one, match): index
                for index, match in enumerate(matches)
            }
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        return [result for result in results if result is not None]


def process_website_override(
    result: SchoolSearchResult,
    website_match: WebsiteMatch,
    settings: SearchSettings,
    progress: ProgressCallback | None = None,
) -> SchoolSearchResult:
    updated = SchoolSearchService(settings, progress).search_match(result.school_match, website_match=website_match)
    if updated.status.startswith("Completed") or updated.status in {"Partial result", "Website blocked crawling"}:
        updated.status = "Website provided by user" if updated.status.startswith("Completed") else updated.status
    return updated
