from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .models import SchoolCandidate, WebsiteCandidate, WebsiteMatch
from .school_matcher import normalize_school_name
from .utilities import is_third_party_domain, normalize_url, registered_domain


def is_valid_official_domain(url: str) -> bool:
    normalized = normalize_url(url)
    if not normalized or is_third_party_domain(normalized):
        return False
    domain = registered_domain(normalized)
    return bool(domain and "." in domain and not domain.endswith(("duckduckgo.com", "google.com", "bing.com")))


def _meaningful_tokens(value: str) -> set[str]:
    ignored = {"school", "schools", "district", "public", "regional", "township", "board", "education", "the", "of"}
    return {token for token in normalize_school_name(value).split() if len(token) > 2 and token not in ignored}


def validate_website_content(
    candidate: WebsiteCandidate,
    school: SchoolCandidate,
    html: str = "",
    final_url: str = "",
) -> WebsiteMatch:
    url = normalize_url(final_url or candidate.url)
    if not is_valid_official_domain(url):
        return WebsiteMatch(
            "Not Found", "Not Found", candidates=[candidate],
            reason="The candidate is a third-party, search, social, or invalid domain.",
        )
    soup = BeautifulSoup(html or "", "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else candidate.title
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:100_000]
    haystack = normalize_school_name(" ".join((title, text, candidate.snippet, url)))
    score = 12.0
    evidence: list[str] = []

    school_tokens = _meaningful_tokens(school.canonical_name)
    district_tokens = _meaningful_tokens(school.district_name)
    hay_tokens = set(haystack.split())
    school_overlap = len(school_tokens & hay_tokens) / max(1, len(school_tokens))
    district_overlap = len(district_tokens & hay_tokens) / max(1, len(district_tokens))
    if school_overlap >= 0.8:
        score += 38
        evidence.append("School name strongly matches the site.")
    elif school_overlap >= 0.5:
        score += 20
        evidence.append("Several school-name terms match the site.")
    if district_overlap >= 0.75:
        score += 32
        evidence.append("District name strongly matches the site.")
    elif district_overlap >= 0.45:
        score += 16
        evidence.append("Several district-name terms match the site.")
    location_terms = [school.municipality, school.county, "New Jersey", "NJ"]
    if any(term and normalize_school_name(term) in haystack for term in location_terms):
        score += 12
        evidence.append("New Jersey or local location evidence appears on the site.")
    if candidate.source.startswith("NJDOE"):
        score += 20
        evidence.append("The URL came from an official NJDOE record.")
    score = min(score, 100.0)
    if score >= 76:
        status = confidence = "Verified"
    elif score >= 55:
        status = confidence = "Likely"
    elif score >= 30:
        status = confidence = "Needs Confirmation"
    else:
        status = confidence = "Not Found"
    return WebsiteMatch(
        status=status,
        confidence=confidence,
        url=url if status != "Not Found" else "",
        source=candidate.source,
        score=round(score, 1),
        evidence=evidence,
        candidates=[candidate],
        reason=" ".join(evidence) or "The site did not contain enough evidence to link it to the school.",
    )


def validate_user_website(url: str, school: SchoolCandidate, html: str = "") -> WebsiteMatch:
    candidate = WebsiteCandidate(url=normalize_url(url), source="User-provided website")
    match = validate_website_content(candidate, school, html=html)
    if match.status == "Not Found" and is_valid_official_domain(candidate.url):
        match.status = match.confidence = "Needs Confirmation"
        match.url = candidate.url
        match.reason = "The domain is valid, but its connection to this school could not be confirmed automatically."
    return match

