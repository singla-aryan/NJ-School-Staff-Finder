from src.models import SchoolCandidate
from src.nj_school_directory import NJSchoolDirectory, OFFICIAL_JSON_URL, OFFLINE_SEED
from src.school_matcher import SchoolMatcher


def test_current_njdoe_json_catalog_mapping():
    records = NJSchoolDirectory._records_from_official_json([
        {
            "state": "NJ",
            "countyCode": "21",
            "countyName": "Mercer",
            "districtCode": "2580",
            "districtName": "Lawrence Township Public School District",
            "schoolCode": "040",
            "schoolName": "Lawrence High School",
            "city": "Lawrenceville",
            "s_website": "https://www.ltps.org",
            "d_website": "www.ltps.org",
            "s_hs_flag": "Y",
        }
    ], OFFICIAL_JSON_URL)
    assert len(records) == 1
    school = records[0]
    assert school.canonical_name == "Lawrence High School"
    assert school.district_name == "Lawrence Township Public School District"
    assert school.county == "Mercer"
    assert school.municipality == "Lawrenceville"
    assert school.school_code == "21-2580-040"
    assert school.school_url == "https://www.ltps.org/"


def test_non_nj_records_are_excluded():
    records = NJSchoolDirectory._records_from_official_json([
        {"state": "PA", "schoolName": "Lawrence High School", "districtName": "Other"}
    ])
    assert records == []


def test_seed_does_not_duplicate_current_official_school_with_renamed_district():
    records = [SchoolCandidate("Princeton High School", "Princeton Public School District", "Mercer")]
    merged = NJSchoolDirectory._merge_seed(records)
    matches = [record for record in merged if record.canonical_name == "Princeton High School"]
    assert len(matches) == 1
    assert SchoolMatcher(merged).match("Princeton High School").status == "Verified"
