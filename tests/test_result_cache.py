from src.models import (
    GeneralContact,
    SchoolCandidate,
    SchoolInput,
    SchoolMatch,
    SchoolSearchResult,
    StaffContact,
    WebsiteCandidate,
    WebsiteMatch,
)
from src.result_cache import load_result, save_result


def completed_result() -> SchoolSearchResult:
    candidate = SchoolCandidate(
        canonical_name="Princeton High School",
        district_name="Princeton Public School District",
        county="Mercer",
        municipality="Princeton",
        school_url="https://www.princetonk12.org/princeton-high-school",
    )
    match = SchoolMatch(
        SchoolInput("Princeton HS", "princeton high school"),
        "Verified",
        "Verified",
        100.0,
        candidate,
    )
    website = WebsiteMatch(
        "Verified",
        "Verified",
        "https://www.princetonk12.org/princeton-high-school",
        candidates=[WebsiteCandidate("https://www.princetonk12.org/counseling", "Search")],
    )
    contact = StaffContact(
        "Jamie Rivera",
        "School Counselor",
        "School Counseling",
        "jrivera@princetonk12.org",
        "https://www.princetonk12.org/counseling",
        evidence_snippet="Jamie Rivera - School Counselor",
        date_checked="2026-07-16T12:00:00-04:00",
        email_confidence="Verified",
        credentials="LPC",
    )
    general = GeneralContact(
        "Counseling Office",
        "School Counseling",
        "counseling@princetonk12.org",
        "https://www.princetonk12.org/counseling",
    )
    return SchoolSearchResult(
        "Princeton HS",
        match,
        website_match=website,
        contacts=[contact],
        general_contacts=[general],
        status="Completed - contacts found",
        pages_searched=14,
        total_pages_discovered=18,
        checked_at="2026-07-16T12:00:00-04:00",
    )


def test_completed_result_round_trips_and_uses_canonical_alias(tmp_path):
    original = completed_result()
    assert save_result(original, tmp_path)

    cached = load_result("Princeton High School", tmp_path)
    assert cached is not None
    assert cached.result.status == "Completed - contacts found"
    assert cached.result.school.canonical_name == "Princeton High School"
    assert cached.result.website_match.candidates[0].url.endswith("/counseling")
    assert cached.result.contacts[0].credentials == "LPC"
    assert cached.result.general_contacts[0].email == "counseling@princetonk12.org"
    assert cached.date_label == "July 16, 2026 at 12:00 PM"


def test_failed_or_unresolved_result_is_not_saved(tmp_path):
    result = completed_result()
    result.status = "Failed with recoverable error"
    assert not save_result(result, tmp_path)
    assert load_result("Princeton HS", tmp_path) is None


def test_corrupt_saved_result_is_ignored(tmp_path):
    result = completed_result()
    assert save_result(result, tmp_path)
    files = list(tmp_path.glob("*.json"))
    assert files
    for path in files:
        path.write_text("not json", encoding="utf-8")
    assert load_result("Princeton HS", tmp_path) is None
