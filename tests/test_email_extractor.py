from src.email_extractor import extract_contacts_from_html, extract_email_evidence, normalize_email


def test_visible_email_extraction_and_association():
    html = """
    <div class="staff-card"><h3>Jane Smith</h3><p>School Counselor</p>
    <p>jane.smith@district.k12.nj.us</p></div>
    """
    staff, general = extract_contacts_from_html(html, "https://district.k12.nj.us/counseling")
    assert len(staff) == 1
    assert staff[0].staff_name == "Jane Smith"
    assert staff[0].role_category == "School Counseling"
    assert staff[0].email == "jane.smith@district.k12.nj.us"
    assert not general


def test_mailto_link_with_email_button_text():
    html = """
    <article><strong>Alex Brown</strong><span>School Psychologist</span>
    <a href="mailto:abrown@district.org?subject=Hello">Email</a></article>
    """
    staff, _ = extract_contacts_from_html(html, "https://district.org/staff")
    assert staff[0].email == "abrown@district.org"
    assert staff[0].extraction_method == "mailto link"


def test_public_obfuscated_email_extraction():
    html = "<p>Maria Jones | School Social Worker | maria.jones [at] district [dot] org</p>"
    staff, _ = extract_contacts_from_html(html, "https://district.org/support")
    assert staff[0].email == "maria.jones@district.org"
    assert staff[0].extraction_method == "public obfuscated text"


def test_incomplete_addresses_are_rejected():
    assert normalize_email("person@district") == ""
    assert normalize_email("p***@district.org") == ""
    assert extract_email_evidence("<p>School Counselor: person@district</p>") == []


def test_guessed_pattern_examples_are_rejected():
    assert normalize_email("first.last@district.org") == ""
    assert normalize_email("jane.doe@example.com") == ""


def test_general_department_contact_is_separate():
    html = "<div>Counseling Office | counseling@district.k12.nj.us</div>"
    staff, general = extract_contacts_from_html(html, "https://district.k12.nj.us/counseling")
    assert staff == []
    assert len(general) == 1
    assert general[0].contact_type == "General Department"


def test_email_without_relevant_role_is_not_reported():
    html = "<div>Superintendent Pat Person — pat.person@district.org</div>"
    staff, general = extract_contacts_from_html(html, "https://district.org/leadership")
    assert not staff and not general


def test_public_json_ld_contact_is_read():
    html = """
    <script type="application/ld+json">
    {"name":"Taylor Green","jobTitle":"Student Assistance Coordinator","email":"tgreen@district.org"}
    </script>
    """
    staff, _ = extract_contacts_from_html(html, "https://district.org/directory")
    assert staff[0].email == "tgreen@district.org"
    assert staff[0].extraction_method == "public structured data"


def test_credentials_are_stored_and_clinical_category_wins():
    html = """
    <div class="staff-card"><h3>Jane Smith, LCSW</h3><p>School Counselor</p>
    <p>jane.smith@district.org</p></div>
    """
    staff, _ = extract_contacts_from_html(html, "https://district.org/counseling")
    assert staff[0].role == "School Counselor"
    assert staff[0].credentials == "LCSW"
    assert staff[0].role_category == "Licensed or Clinical Mental-Health Professionals"


def test_spelled_out_license_is_preserved_as_a_credential():
    html = "<div>Kim Ray | Licensed Professional Counselor | kim.ray@district.org</div>"
    staff, _ = extract_contacts_from_html(html, "https://district.org/mental-health")
    assert staff[0].credentials == "Licensed Professional Counselor"


def test_ambiguous_support_title_is_marked_for_review():
    html = "<div><b>Sam Jones</b> | Case Manager | sam.jones@district.org</div>"
    staff, _ = extract_contacts_from_html(html, "https://district.org/support")
    assert staff[0].role_category == "Possible Relevant Staff Requiring Review"
    assert staff[0].email_confidence == "Needs Review"


def test_counselor_roster_page_associates_named_sections_without_repeating_role():
    html = """
    <html><head><title>Meet Our Counselors - Example High School</title></head><body>
      <h1>Meet Our Counselors</h1>
      <section><h2>Meghan Brennan</h2><p>MeghanBrennan@district.org</p>
      <p>Welcome! I support students and families throughout high school.</p></section>
      <section><h2>Daniel DeStefano</h2><p>DanielDeStefano@district.org</p>
      <p>Welcome to my page. Please stop by whenever you need help.</p></section>
      <footer><p>Site Feedback</p><p>webmaster@district.org</p></footer>
    </body></html>
    """
    staff, general = extract_contacts_from_html(
        html,
        "https://district.org/high-school/counseling/meet-our-counselors",
        "Meet Our Counselors - Example High School",
    )
    assert [(item.staff_name, item.role, item.email) for item in staff] == [
        ("Meghan Brennan", "School Counselor", "meghanbrennan@district.org"),
        ("Daniel DeStefano", "School Counselor", "danieldestefano@district.org"),
    ]
    assert general == []


def test_counselor_roster_uses_visible_tab_label_for_profile_name():
    html = """
    <html><head><title>Meet the Counselors</title></head><body>
      <h1>Meet the Counselors</h1>
      <a role="tab" aria-controls="fsEl_42">Angelo Costagliola</a>
      <section id="fsEl_42"><p>PMS Counselor</p><p>AngeloCostagliola@district.org</p>
      <p>Hello! I am proud to serve as one of your school counselors.</p></section>
      <a role="tab" aria-controls="fsEl_43">Thomas Filippone</a>
      <section id="fsEl_43"><p>PHS Counselor</p><p>TomFilippone@district.org</p></section>
    </body></html>
    """
    staff, _ = extract_contacts_from_html(
        html,
        "https://district.org/middle-school/counseling/meet-the-counselors",
        "Meet the Counselors",
    )
    assert len(staff) == 2
    assert staff[0].staff_name == "Angelo Costagliola"
    assert staff[0].role == "School Counselor"
    assert staff[1].staff_name == "Thomas Filippone"


def test_long_biography_keeps_nearby_mental_health_role():
    biography = "I support students and families. " * 70
    html = f"""
    <section><h2>Bethzayda Matos</h2><p>Mental Health Counselor</p>
    <p>BethzaydaMatos@district.org</p><p>{biography}</p></section>
    """
    staff, _ = extract_contacts_from_html(html, "https://district.org/counseling/department-staff")
    assert len(staff) == 1
    assert staff[0].staff_name == "Bethzayda Matos"
    assert staff[0].role == "Mental Health Counselor"


def test_transcript_secretaries_are_not_misclassified_as_counselors():
    html = """
    <h1>Requesting A Transcript</h1>
    <p>FOR CURRENT STUDENTS: If you still need a transcript, please request it through the Counseling Office.</p>
    <p>Submit the form to Lori Scala (A-L), LoriScala@district.org, or
    Veronica Foreman (M-Z), VeronicaForeman@district.org.</p>
    """
    staff, general = extract_contacts_from_html(
        html,
        "https://district.org/high-school/academics/school-counseling/transcripts",
        "Transcripts",
    )
    assert staff == []
    assert general == []
