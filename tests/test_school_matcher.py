from src.models import SchoolCandidate
from src.school_matcher import SchoolMatcher, normalize_school_name, split_school_name_and_hint


def test_school_name_normalization_and_hs_expansion():
    assert normalize_school_name("  Princeton   HS. ") == "princeton high school"


def test_hyphen_punctuation_and_apostrophe_normalization():
    assert normalize_school_name("West Windsor–Plainsboro's HS") == "west windsor plainsboros high school"
    assert normalize_school_name("West Windsor-Plainsboro HS") == "west windsor plainsboro high school"


def test_directional_words_are_preserved():
    assert normalize_school_name("Central High School South") != normalize_school_name("Central High School North")


def test_exact_school_match():
    candidates = [SchoolCandidate("Princeton High School", "Princeton Public Schools", "Mercer")]
    match = SchoolMatcher(candidates).match("Princeton HS")
    assert match.status == "Verified"
    assert match.candidate == candidates[0]


def test_ambiguous_school_match_is_not_silently_selected():
    candidates = [
        SchoolCandidate("Washington Elementary School", "District A", "Bergen"),
        SchoolCandidate("Washington Elementary School", "District B", "Essex"),
    ]
    match = SchoolMatcher(candidates).match("Washington Elementary School")
    assert match.status == "Needs Review"
    assert match.candidate is None
    assert len(match.alternatives) == 2


def test_directional_mismatch_does_not_auto_select():
    candidates = [
        SchoolCandidate("Regional High School North", "Example District"),
        SchoolCandidate("Regional High School South", "Example District"),
    ]
    match = SchoolMatcher(candidates).match("Regional High School")
    assert match.status in {"Needs Review", "Not Found"}


def test_municipality_suffix_is_used_as_a_hint_not_part_of_name():
    assert split_school_name_and_hint("Robbinsville High School, Robbinsville") == (
        "Robbinsville High School",
        "Robbinsville",
    )
    candidates = [SchoolCandidate("Robbinsville High School", "Robbinsville Public School District")]
    assert SchoolMatcher(candidates).match("Robbinsville High School, Robbinsville").status == "Verified"


def test_unrelated_high_schools_are_not_offered_as_alternatives():
    candidates = [
        SchoolCandidate("Princeton High School", "Princeton Public Schools"),
        SchoolCandidate("Edison High School", "Edison Township Public Schools"),
    ]
    match = SchoolMatcher(candidates).match("Robbinsville High School, Robbinsville")
    assert match.status == "Not Found"
    assert match.alternatives == []
