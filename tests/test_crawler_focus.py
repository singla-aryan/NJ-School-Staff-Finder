import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.robotparser import RobotFileParser

import httpx

from src.crawler import SiteCrawler
from src.html_parser import parse_html_page
from src.models import SearchSettings


def allow_all_robots():
    parser = RobotFileParser()
    parser.parse([])
    return parser


def test_default_search_has_no_fixed_page_ceiling():
    assert SearchSettings().max_pages is None


def test_irrelevant_pdfs_and_low_value_sections_do_not_enter_frontier():
    parsed = parse_html_page(
        """
        <a href="/assets/counseling-staff-directory.pdf">Counseling directory</a>
        <a href="/assets/summer-calculus-assignment.pdf">Summer calculus assignment</a>
        <a href="/athletics/schedule">Athletics</a>
        <a href="/academics">Academics</a>
        """,
        "https://school.example/",
    )
    crawler = SiteCrawler(SearchSettings())
    relevant_pdf = "https://school.example/assets/counseling-staff-directory.pdf"
    summer_pdf = "https://school.example/assets/summer-calculus-assignment.pdf"
    athletics = "https://school.example/athletics/schedule"
    academics = "https://school.example/academics"
    assert relevant_pdf in parsed.priority_links
    assert crawler._link_priority(relevant_pdf, 1, parsed) is not None
    assert crawler._link_priority(summer_pdf, 1, parsed) is None
    assert crawler._link_priority(athletics, 1, parsed) is None
    assert crawler._link_priority(academics, 1, parsed) is not None
    allowed_hosts = {"school.example", "example", "www.example"}
    assert crawler._is_allowed_url("https://other-school.example/staff", "example", allowed_hosts)
    assert not crawler._is_allowed_url("https://unrelated.example.org/staff", "example", allowed_hosts)


def test_role_specific_profile_beats_generic_navigation():
    parsed = parse_html_page(
        """
        <div class="staff-card">
          <a href="/profile/toni-anthony">Toni Anthony</a>
          <span>Student Assistance Counselor</span>
        </div>
        <a href="/departments">Departments</a>
        """,
        "https://school.example/staff/",
    )
    profile = "https://school.example/profile/toni-anthony"
    assert profile in parsed.role_links
    assert SiteCrawler(SearchSettings())._link_priority(profile, 2, parsed) == -38


def test_broad_navigation_container_does_not_mark_every_link_relevant():
    parsed = parse_html_page(
        """
        <nav>
          <a href="/counseling">Counseling</a>
          <a href="/math">Mathematics</a>
          <a href="/science">Science</a>
          <a href="/music">Music</a>
        </nav>
        """,
        "https://school.example/",
    )
    assert "https://school.example/counseling" in parsed.support_links
    assert "https://school.example/math" not in parsed.priority_links
    assert "https://school.example/science" not in parsed.priority_links


def test_directory_opens_only_role_labelled_profiles(monkeypatch):
    fetched: list[str] = []

    def fake_get(self, client, url):
        fetched.append(url)
        path = url.rstrip("/")
        if path == "https://school.example":
            body = '<a href="/staff">Staff Directory</a>'
        elif path == "https://school.example/staff":
            ordinary = "".join(
                f'<li><a href="/apps/pages/index.jsp?uREC_ID={index}">Person {index}</a><span>Teacher</span></li>'
                for index in range(100)
            )
            body = ordinary + (
                '<li><a href="/apps/pages/index.jsp?uREC_ID=999">Alex Rivera</a>'
                '<span>School Psychologist</span></li>'
            )
        else:
            body = "<html><body>Public staff profile</body></html>"
        return httpx.Response(
            200,
            content=body.encode(),
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(SiteCrawler, "_get", fake_get)
    monkeypatch.setattr(SiteCrawler, "_robots", lambda self, client, root: allow_all_robots())
    monkeypatch.setattr(SiteCrawler, "_sitemap_candidates", lambda self, client, root, robots, allowed_hosts=None: [])
    outcome = SiteCrawler(SearchSettings(max_depth=4, rate_limit_seconds=0)).crawl(
        "https://school.example/"
    )
    assert len(outcome.pages) == 3
    assert any("uREC_ID=999" in url for url in fetched)
    assert not any("uREC_ID=50" in url for url in fetched)


def test_district_scope_allows_sibling_school_sections():
    crawler = SiteCrawler(SearchSettings())
    allowed_hosts = {"district.org"}
    assert crawler._is_allowed_url(
        "https://district.org/princeton-high-school/counseling",
        "district.org",
        allowed_hosts,
    )
    assert crawler._is_allowed_url(
        "https://district.org/families/student-services",
        "district.org",
        allowed_hosts,
    )
    assert crawler._is_allowed_url(
        "https://middle-school.district.org/counseling",
        "district.org",
        allowed_hosts,
    )


def test_populated_edlio_staff_profile_is_accepted_as_soft_404():
    html = (
        '<dl class="primary-info"><dd class="user-name">Toni Anthony</dd>'
        '<dd class="position-user-page">CHS - SAC</dd></dl>'
    )
    assert SiteCrawler._is_public_staff_profile(html)
    assert not SiteCrawler._is_public_staff_profile("<h1>Page not found</h1>")


def test_crawler_fetches_two_relevant_pages_concurrently(monkeypatch, tmp_path):
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def fake_get(self, client, url):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        if url.rstrip("/") == "https://school.example":
            body = (
                '<a href="/counseling">Counseling</a>'
                '<a href="/school-psychologist">School Psychologist</a>'
            )
        else:
            body = "<html><body>Relevant department page</body></html>"
        with lock:
            active -= 1
        return httpx.Response(
            200,
            content=body.encode(),
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(SiteCrawler, "_get", fake_get)
    monkeypatch.setattr(SiteCrawler, "_robots", lambda self, client, root: allow_all_robots())
    monkeypatch.setattr(SiteCrawler, "_sitemap_candidates", lambda self, client, root, robots, allowed_hosts=None: [])
    outcome = SiteCrawler(SearchSettings(max_pages=10, max_depth=2, rate_limit_seconds=0)).crawl(
        "https://school.example/"
    )
    assert len(outcome.pages) == 3
    assert maximum_active == 2


def test_domain_connection_limit_is_shared_across_school_crawlers(monkeypatch, tmp_path):
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    class FakeClient:
        def get(self, url):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return httpx.Response(
                200,
                content=b"<html>ok</html>",
                headers={"content-type": "text/html"},
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(
        "src.crawler.cache_path",
        lambda url: tmp_path / f"{abs(hash(url))}.json",
    )
    crawlers = [SiteCrawler(SearchSettings(rate_limit_seconds=0)) for _ in range(4)]
    client = FakeClient()
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(crawler._get, client, f"https://school.example/page-{index}")
            for index, crawler in enumerate(crawlers)
        ]
        for future in futures:
            assert future.result().status_code == 200
    assert maximum_active == 2
