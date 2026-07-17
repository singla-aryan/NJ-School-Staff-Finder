from __future__ import annotations

from collections.abc import Iterable

import httpx

from .models import SchoolCandidate, WebsiteCandidate, WebsiteMatch
from .utilities import dedupe_preserve_order, is_third_party_domain, normalize_url
from .website_validator import is_valid_official_domain, validate_website_content


class WebsiteFinder:
    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        self.attempts: list[str] = []

    def find(self, school: SchoolCandidate, broad: bool = False) -> WebsiteMatch:
        self.attempts = []
        candidates: list[WebsiteCandidate] = []
        if school.school_url:
            candidates.append(WebsiteCandidate(school.school_url, "NJDOE school record"))
            self.attempts.append("Checked the school URL in the NJDOE record.")
        if school.district_url:
            candidates.append(WebsiteCandidate(school.district_url, "NJDOE district record"))
            self.attempts.append("Checked the district URL in the NJDOE record.")

        queries = self._queries(school, broad=broad)
        for query in queries:
            self.attempts.append(f"Public search: {query}")
            candidates.extend(self._search(query))
        candidates = self._dedupe_candidates(candidates)

        evaluated: list[WebsiteMatch] = []
        headers = {"User-Agent": "NJSchoolStudentSupportFinder/1.0 (public professional contact research)"}
        with httpx.Client(timeout=self.timeout, follow_redirects=True, max_redirects=5, headers=headers) as client:
            for candidate in candidates[:24]:
                if not is_valid_official_domain(candidate.url):
                    continue
                try:
                    response = client.get(candidate.url)
                    content_type = response.headers.get("content-type", "")
                    html = response.text if "html" in content_type or not content_type else ""
                    match = validate_website_content(candidate, school, html=html, final_url=str(response.url))
                except (httpx.HTTPError, UnicodeError) as exc:
                    match = validate_website_content(candidate, school)
                    match.reason += f" The home page could not be read during validation ({type(exc).__name__})."
                evaluated.append(match)
                if match.status == "Verified":
                    match.candidates = candidates
                    return match

        evaluated.sort(key=lambda match: match.score, reverse=True)
        if evaluated and evaluated[0].status == "Likely":
            evaluated[0].candidates = candidates
            return evaluated[0]
        if evaluated and evaluated[0].status == "Needs Confirmation":
            evaluated[0].candidates = candidates
            return evaluated[0]
        return WebsiteMatch(
            "Not Found", "Not Found", candidates=candidates,
            reason="NJDOE records and public searches did not produce a website with enough official-school evidence.",
        )

    def _search(self, query: str) -> list[WebsiteCandidate]:
        try:
            from ddgs import DDGS
            results = DDGS(timeout=self.timeout).text(query, max_results=6)
        except Exception:
            return []
        candidates = []
        for result in results or []:
            url = normalize_url(str(result.get("href") or result.get("url") or ""))
            if url and not is_third_party_domain(url):
                candidates.append(WebsiteCandidate(
                    url=url,
                    source=f"Public search: {query}",
                    title=str(result.get("title") or ""),
                    snippet=str(result.get("body") or result.get("snippet") or ""),
                ))
        return candidates

    @staticmethod
    def _queries(school: SchoolCandidate, broad: bool = False) -> list[str]:
        base = [
            f'"{school.canonical_name}" "{school.district_name}" New Jersey official',
            f'"{school.canonical_name}" "New Jersey"',
        ]
        if school.municipality:
            base.append(f'"{school.canonical_name}" "{school.municipality}" NJ')
        terms = [
            "counseling", "student services", "staff directory", "mental health therapist",
            "school psychologist", "school social worker", "student assistance coordinator",
        ]
        if broad:
            terms += ["child study team", "guidance", "pupil services", "crisis counselor", "clinical social worker"]
        base.extend(f'"{school.canonical_name}" {term} New Jersey' for term in terms)
        if school.district_name:
            district_terms = [
                "staff directory", "student services", "counseling", "mental health",
                "school psychology", "school social work",
            ]
            if broad:
                district_terms += ["pupil services", "child study team"]
            base.extend(f'"{school.district_name}" {term}' for term in district_terms)
        return dedupe_preserve_order(base)

    @staticmethod
    def _dedupe_candidates(candidates: Iterable[WebsiteCandidate]) -> list[WebsiteCandidate]:
        unique: dict[str, WebsiteCandidate] = {}
        for candidate in candidates:
            normalized = normalize_url(candidate.url)
            if normalized and normalized not in unique:
                candidate.url = normalized
                unique[normalized] = candidate
        return list(unique.values())
