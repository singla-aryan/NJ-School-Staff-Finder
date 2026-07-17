from src.role_matcher import detect_role, extract_credentials, role_sort_key


def test_specific_role_detection():
    match = detect_role("Jordan Lee — Student Assistance Coordinator")
    assert match is not None
    assert match.category == "Student Assistance and Substance-Use Support"
    assert match.role == "Student Assistance Coordinator"


def test_child_study_team_role_detection():
    match = detect_role("Case Manager, Child Study Team")
    assert match is not None
    assert match.category == "Possible Relevant Staff Requiring Review"
    assert match.review_required


def test_false_positive_roles_are_rejected():
    assert detect_role("Outside vendor advertisement for a camp counselor") is None
    assert detect_role("Board attorney and general counsel") is None
    assert detect_role("Financial counselor for tuition sales") is None
    assert detect_role("Occupational Therapist") is None


def test_selected_categories_are_respected():
    assert detect_role("School Psychologist", ["School Social Work"]) is None


def test_clinical_license_overrides_school_counseling_category():
    match = detect_role("Jane Smith - School Counselor, LCSW")
    assert match is not None
    assert match.category == "Licensed or Clinical Mental-Health Professionals"
    assert extract_credentials("Jane Smith, L.C.S.W., LPC") == "LCSW, LPC"


def test_college_or_career_only_counselors_are_excluded():
    assert detect_role("College and Career Counselor") is None
    assert detect_role("Admissions Counselor") is None
    assert detect_role("School Counselor and College Adviser") is not None


def test_ambiguous_special_services_leader_requires_relevant_evidence():
    assert detect_role("Director of Special Services") is None
    match = detect_role("Director of Special Services overseeing the Child Study Team")
    assert match is not None
    assert match.category == "Mental-Health and Student-Services Leadership"


def test_required_group_order_puts_clinical_before_counseling_and_leadership():
    assert role_sort_key("Licensed or Clinical Mental-Health Professionals") < role_sort_key("School Psychology")
    assert role_sort_key("School Psychology") < role_sort_key("School Counseling")
    assert role_sort_key("School Counseling") < role_sort_key("Mental-Health and Student-Services Leadership")
