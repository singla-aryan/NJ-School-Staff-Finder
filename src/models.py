from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable


@dataclass(slots=True)
class SchoolInput:
    raw_name: str
    normalized_name: str = ""


@dataclass(slots=True)
class SchoolCandidate:
    canonical_name: str
    district_name: str = ""
    county: str = ""
    municipality: str = ""
    school_type: str = ""
    grade_range: str = ""
    school_url: str = ""
    district_url: str = ""
    source_url: str = "https://homeroom6.doe.nj.gov/directory/"
    school_code: str = ""
    district_code: str = ""

    @property
    def label(self) -> str:
        parts = [self.canonical_name, self.district_name, self.municipality, self.county]
        return " — ".join(part for part in parts if part)


@dataclass(slots=True)
class SchoolMatch:
    input_school: SchoolInput
    status: str
    confidence: str
    score: float = 0.0
    candidate: SchoolCandidate | None = None
    alternatives: list[SchoolCandidate] = field(default_factory=list)
    reason: str = ""


@dataclass(slots=True)
class WebsiteCandidate:
    url: str
    source: str
    title: str = ""
    snippet: str = ""


@dataclass(slots=True)
class WebsiteMatch:
    status: str
    confidence: str
    url: str = ""
    source: str = ""
    score: float = 0.0
    evidence: list[str] = field(default_factory=list)
    candidates: list[WebsiteCandidate] = field(default_factory=list)
    reason: str = ""


@dataclass(slots=True)
class CrawlPage:
    url: str
    title: str = ""
    text: str = ""
    html: str = ""
    content_type: str = "text/html"
    depth: int = 0
    status_code: int = 200
    extraction_method: str = "httpx"
    relevant: bool = False


@dataclass(slots=True)
class StaffContact:
    staff_name: str
    role: str
    role_category: str
    email: str
    source_url: str
    page_title: str = ""
    evidence_snippet: str = ""
    extraction_method: str = "visible text"
    date_checked: str = ""
    email_confidence: str = "Needs Review"
    source_urls: list[str] = field(default_factory=list)
    contact_type: str = "Named Staff"
    credentials: str = ""
    school: str = ""
    district: str = ""

    def __post_init__(self) -> None:
        if not self.source_urls and self.source_url:
            self.source_urls = [self.source_url]


@dataclass(slots=True)
class GeneralContact:
    department_name: str
    role_category: str
    email: str
    source_url: str
    page_title: str = ""
    evidence_snippet: str = ""
    extraction_method: str = "visible text"
    date_checked: str = ""
    email_confidence: str = "Verified"
    source_urls: list[str] = field(default_factory=list)
    contact_type: str = "General Department"

    def __post_init__(self) -> None:
        if not self.source_urls and self.source_url:
            self.source_urls = [self.source_url]


@dataclass(slots=True)
class UnresolvedSchool:
    input_school_name: str
    canonical_school_name: str = ""
    district: str = ""
    municipality: str = ""
    county: str = ""
    reason: str = ""
    discovery_attempts: list[str] = field(default_factory=list)
    candidates: list[WebsiteCandidate] = field(default_factory=list)


@dataclass(slots=True)
class SchoolSearchResult:
    input_school_name: str
    school_match: SchoolMatch
    website_match: WebsiteMatch | None = None
    contacts: list[StaffContact] = field(default_factory=list)
    general_contacts: list[GeneralContact] = field(default_factory=list)
    review_contacts: list[StaffContact] = field(default_factory=list)
    status: str = "Pending"
    pages_searched: int = 0
    total_pages_discovered: int = 0
    relevant_pages: int = 0
    pdfs_inspected: int = 0
    scanned_pdfs: list[str] = field(default_factory=list)
    javascript_used: bool = False
    crawl_restricted: bool = False
    page_limit_reached: bool = False
    errors: list[str] = field(default_factory=list)
    debug_details: list[str] = field(default_factory=list)
    discovery_attempts: list[str] = field(default_factory=list)
    checked_at: str = ""

    @property
    def school(self) -> SchoolCandidate | None:
        return self.school_match.candidate

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SearchSettings:
    selected_categories: list[str] = field(default_factory=list)
    include_general_contacts: bool = True
    use_javascript: bool = True
    search_pdfs: bool = True
    max_pages: int | None = None
    max_depth: int = 4
    timeout: float = 15.0
    show_review_contacts: bool = False
    rate_limit_seconds: float = 0.5


ProgressCallback = Callable[[str, dict[str, Any]], None]
