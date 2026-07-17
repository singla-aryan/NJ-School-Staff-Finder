import threading
import time

from src.crawler import CrawlOutcome
from src.exporters import contacts_dataframe, excel_bytes
from src.models import (
    GeneralContact,
    SchoolCandidate,
    SchoolInput,
    SchoolMatch,
    SchoolSearchResult,
    SearchSettings,
    StaffContact,
    WebsiteMatch,
)
from src.result_processor import (
    SchoolSearchService,
    deduplicate_contacts,
    process_crawl_outcome,
    process_website_override,
)


def make_match(name="Example High School"):
    candidate = SchoolCandidate(name, "Example District", "Mercer", "Exampleville")
    return SchoolMatch(SchoolInput(name, name.casefold()), "Verified", "Verified", 100, candidate)


def contact(source="https://district.org/a", role="School Counselor"):
    return StaffContact(
        "Jamie Rivera", role, "School Counseling", "jrivera@district.org", source,
        email_confidence="Verified", source_urls=[source],
    )


def test_deduplication_preserves_all_sources_and_strongest_role():
    first = contact("https://district.org/a", "Counselor")
    second = contact("https://district.org/b", "Director of Counseling")
    results = deduplicate_contacts([first, second])
    assert len(results) == 1
    assert results[0].role == "Director of Counseling"
    assert results[0].source_urls == ["https://district.org/a", "https://district.org/b"]


def test_grouped_result_processing_and_general_separation():
    result = SchoolSearchResult("Example High School", make_match(), website_match=WebsiteMatch("Verified", "Verified", "https://district.org"))
    general = GeneralContact("Counseling Office", "School Counseling", "counseling@district.org", "https://district.org/counseling")
    outcome = CrawlOutcome(
        contacts=[contact(), contact("https://district.org/b")],
        general_contacts=[general],
        total_pages_discovered=12,
    )
    processed = process_crawl_outcome(result, outcome, SearchSettings(include_general_contacts=True))
    assert len(processed.contacts) == 1
    assert len(processed.general_contacts) == 1
    assert processed.total_pages_discovered == 12
    frame = contacts_dataframe([processed])
    assert set(frame["Contact Type"]) == {"Named Staff", "General Department"}
    assert set(frame["Canonical School Name"]) == {"Example High School"}
    assert set(frame["School"]) == {"Example High School"}
    assert "Professional Category" in frame.columns
    assert "Credentials or Licenses" in frame.columns
    assert processed.contacts[0].school == "Example High School"
    assert processed.contacts[0].district == "Example District"


def test_one_school_failure_does_not_stop_batch(monkeypatch):
    good = make_match("Good High School")
    bad = make_match("Bad High School")
    service = SchoolSearchService(SearchSettings())

    def fake_search(match, website_match=None, broad=False):
        if match.input_school.raw_name.startswith("Bad"):
            raise RuntimeError("mock failure")
        return SchoolSearchResult(match.input_school.raw_name, match, status="Completed — no public contacts found")

    monkeypatch.setattr(service, "search_match", fake_search)
    results = service.process_batch([good, bad])
    assert len(results) == 2
    assert results[0].status == "Completed — no public contacts found"
    assert results[1].status == "Failed with recoverable error"


def test_multiple_schools_are_processed_concurrently(monkeypatch):
    service = SchoolSearchService(SearchSettings())
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def fake_search(match, website_match=None, broad=False):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return SchoolSearchResult(match.input_school.raw_name, match, status="Completed")

    monkeypatch.setattr(service, "search_match", fake_search)
    matches = [make_match(f"School {index}") for index in range(5)]
    results = service.process_batch(matches)
    assert maximum_active == 3
    assert [result.input_school_name for result in results] == [f"School {index}" for index in range(5)]


def test_website_override_processing_uses_confirmed_site(monkeypatch):
    original = SchoolSearchResult("Example High School", make_match(), status="Website not found")
    website = WebsiteMatch("Likely", "Likely", "https://district.org", "User-provided website")
    monkeypatch.setattr(
        "src.result_processor.SiteCrawler.crawl",
        lambda self, url, seed_urls=None: CrawlOutcome(contacts=[contact("https://district.org/staff")]),
    )
    updated = process_website_override(original, website, SearchSettings())
    assert updated.status == "Website provided by user"
    assert updated.contacts[0].email == "jrivera@district.org"


def test_ambiguous_role_is_kept_out_of_verified_results():
    possible = StaffContact(
        "Sam Jones",
        "Case Manager",
        "Possible Relevant Staff Requiring Review",
        "sam.jones@district.org",
        "https://district.org/support",
        email_confidence="Needs Review",
    )
    result = SchoolSearchResult("Example High School", make_match())
    processed = process_crawl_outcome(result, CrawlOutcome(contacts=[possible]), SearchSettings())
    assert processed.contacts == []
    assert processed.review_contacts[0].email == "sam.jones@district.org"
    assert processed.status == "Completed - possible contacts need review"


def test_district_wide_listing_labels_each_contacts_actual_school():
    target = contact("https://district.org/quick-links/hib-anti-bullying-information")
    target.evidence_snippet = "Example High School | Jamie Rivera, School Counselor | jrivera@district.org"
    other = StaffContact(
        "Alex Morgan",
        "School Counselor",
        "School Counseling",
        "amorgan@district.org",
        "https://district.org/quick-links/hib-anti-bullying-information",
        evidence_snippet="Example Middle School | Alex Morgan, School Counselor | amorgan@district.org",
        email_confidence="Verified",
    )
    result = SchoolSearchResult("Example High School", make_match())
    processed = process_crawl_outcome(result, CrawlOutcome(contacts=[target, other]), SearchSettings())
    assert {item.email: item.school for item in processed.contacts} == {
        "jrivera@district.org": "Example High School",
        "amorgan@district.org": "Example Middle School",
    }


def test_direct_roster_source_beats_hib_duplicate():
    hib = contact("https://district.org/quick-links/hib-anti-bullying-information")
    direct = contact("https://district.org/example-high-school/counseling/staff")
    direct.evidence_snippet = "Jamie Rivera | School Counselor | jrivera@district.org"
    result = SchoolSearchResult("Example High School", make_match())
    processed = process_crawl_outcome(result, CrawlOutcome(contacts=[hib, direct]), SearchSettings())
    assert len(processed.contacts) == 1
    assert processed.contacts[0].source_url.endswith("/counseling/staff")


def test_excel_export_has_content():
    result = SchoolSearchResult("Example High School", make_match(), contacts=[contact()], status="Completed — contacts found")
    output = excel_bytes([result])
    assert output.startswith(b"PK")
    assert len(output) > 1_000
