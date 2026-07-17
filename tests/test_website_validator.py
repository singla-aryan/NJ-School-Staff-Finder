from src.models import SchoolCandidate, WebsiteCandidate
from src.website_finder import WebsiteFinder
from src.website_validator import is_valid_official_domain, validate_user_website, validate_website_content


def school():
    return SchoolCandidate(
        "Example High School", "Example Township Public Schools", "Mercer", "Exampleville"
    )


def test_official_domain_validation_with_matching_content():
    html = """
    <html><head><title>Example High School</title></head>
    <body>Example Township Public Schools · Exampleville, New Jersey</body></html>
    """
    match = validate_website_content(
        WebsiteCandidate("https://www.exampleps.org/ehs", "Public search"), school(), html
    )
    assert match.status == "Verified"


def test_third_party_directories_are_rejected():
    assert not is_valid_official_domain("https://www.greatschools.org/new-jersey/example/1-Example-High/")
    match = validate_website_content(
        WebsiteCandidate("https://www.niche.com/k12/example-high-school", "search"), school(),
        "<title>Example High School</title>",
    )
    assert match.status == "Not Found"


def test_unrelated_user_domain_requires_confirmation():
    match = validate_user_website(
        "https://unrelated-valid-domain.org/",
        school(),
        "<html><title>Completely Different Organization</title><body>California</body></html>",
    )
    assert match.status == "Needs Confirmation"


def test_website_finder_uses_mocked_http_response(monkeypatch):
    class FakeResponse:
        status_code = 200
        url = "https://www.exampleps.org/"
        text = "<title>Example High School</title><p>Example Township Public Schools, New Jersey</p>"
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def get(self, url):
            return FakeResponse()

    candidate_school = school()
    candidate_school.school_url = "https://www.exampleps.org/"
    monkeypatch.setattr("src.website_finder.httpx.Client", FakeClient)
    monkeypatch.setattr(WebsiteFinder, "_search", lambda self, query: [])
    match = WebsiteFinder().find(candidate_school)
    assert match.status == "Verified"
    assert match.url == "https://www.exampleps.org/"

