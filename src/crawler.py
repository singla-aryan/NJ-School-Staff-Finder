from __future__ import annotations

import heapq
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .html_parser import (
    ParsedHTML,
    is_bridge_url,
    is_directory_url,
    is_low_value_url,
    is_pdf_url,
    is_priority_url,
    is_support_topic_url,
    parse_html_page,
    sitemap_links,
)
from .models import CrawlPage, GeneralContact, ProgressCallback, SearchSettings, StaffContact
from .pdf_parser import parse_pdf
from .utilities import cache_path, normalize_url, read_json_cache, registered_domain, same_domain, utc_now, write_json_cache

USER_AGENT = "NJSchoolStudentSupportFinder/1.0 (+public professional contact research; respects robots.txt)"

_DOMAIN_STATE_LOCK = threading.Lock()
_DOMAIN_RATE_LOCKS: dict[str, threading.Lock] = {}
_DOMAIN_SEMAPHORES: dict[str, threading.BoundedSemaphore] = {}
_DOMAIN_LAST_REQUEST: dict[str, float] = {}
_CACHE_WRITE_LOCK = threading.Lock()


@dataclass(slots=True)
class CrawlOutcome:
    pages: list[CrawlPage] = field(default_factory=list)
    total_pages_discovered: int = 0
    contacts: list[StaffContact] = field(default_factory=list)
    general_contacts: list[GeneralContact] = field(default_factory=list)
    relevant_pages: int = 0
    pdfs_inspected: int = 0
    scanned_pdfs: list[str] = field(default_factory=list)
    javascript_used: bool = False
    restricted: bool = False
    page_limit_reached: bool = False
    errors: list[str] = field(default_factory=list)


class SiteCrawler:
    def __init__(self, settings: SearchSettings, progress: ProgressCallback | None = None):
        self.settings = settings
        self.progress = progress or (lambda _stage, _data: None)

    def crawl(self, start_url: str, seed_urls: list[str] | None = None) -> CrawlOutcome:
        root = normalize_url(start_url)
        outcome = CrawlOutcome()
        if not root:
            outcome.errors.append("The official website URL was invalid.")
            return outcome
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5"}
        root_domain = registered_domain(root)
        root_host = (urlparse(root).hostname or "").casefold()
        allowed_hosts = {root_host, root_domain, f"www.{root_domain}"}
        focused_seeds: list[str] = []
        for seed in seed_urls or []:
            normalized_seed = normalize_url(seed)
            if not normalized_seed or registered_domain(normalized_seed) != root_domain:
                continue
            seed_host = (urlparse(normalized_seed).hostname or "").casefold()
            allowed_hosts.add(seed_host)
            focused_seeds.append(normalized_seed)
        with httpx.Client(
            timeout=self.settings.timeout,
            follow_redirects=True,
            max_redirects=6,
            headers=headers,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
        ) as client, ThreadPoolExecutor(max_workers=2, thread_name_prefix="school-crawl") as executor:
            robots = self._robots(client, root)
            queue: list[tuple[int, int, int, str]] = []
            counter = 0
            heapq.heappush(queue, (0, 0, counter, root))
            discovered: set[str] = {root}
            for link in focused_seeds:
                if link in discovered:
                    continue
                discovered.add(link)
                counter += 1
                heapq.heappush(queue, (-30, 0, counter, link))
            for link in self._sitemap_candidates(client, root, robots, allowed_hosts):
                link = normalize_url(link)
                if (
                    not link
                    or link in discovered
                    or not self._is_allowed_url(link, root_domain, allowed_hosts)
                ):
                    continue
                discovered.add(link)
                counter += 1
                heapq.heappush(queue, (-2 if is_priority_url(link) else 1, 1, counter, link))
            seen: set[str] = set()
            while queue and (
                self.settings.max_pages is None
                or len(outcome.pages) < self.settings.max_pages
            ):
                batch: list[tuple[str, int]] = []
                remaining_slots = (
                    2
                    if self.settings.max_pages is None
                    else self.settings.max_pages - len(outcome.pages)
                )
                while queue and len(batch) < min(2, remaining_slots):
                    _, depth, _, url = heapq.heappop(queue)
                    url = normalize_url(url)
                    if not url or url in seen or depth > self.settings.max_depth or not self._is_allowed_url(url, root_domain, allowed_hosts):
                        continue
                    seen.add(url)
                    if not robots.can_fetch(USER_AGENT, url):
                        outcome.restricted = True
                        continue
                    if any(part in url.casefold() for part in ("/login", "/signin", "/account", "captcha")):
                        continue
                    batch.append((url, depth))
                if not batch:
                    continue

                futures = {executor.submit(self._get, client, url): (url, depth) for url, depth in batch}
                for future in as_completed(futures):
                    url, depth = futures[future]
                    try:
                        response = future.result()
                    except Exception as exc:
                        outcome.errors.append(f"Could not read {url}: {type(exc).__name__}: {exc}")
                        continue
                    try:
                        final_url = normalize_url(str(response.url))
                        if not self._is_allowed_url(final_url, root_domain, allowed_hosts):
                            continue
                        if final_url in seen and final_url != url:
                            continue
                        seen.add(final_url)
                        if response.status_code in {401, 403, 407, 429}:
                            outcome.restricted = True
                            outcome.errors.append(f"Access was restricted for {url} (HTTP {response.status_code}).")
                            continue
                        content_type = response.headers.get("content-type", "").casefold()
                        useful_soft_404 = (
                            response.status_code == 404
                            and "html" in content_type
                            and self._is_public_staff_profile(response.text)
                        )
                        if not useful_soft_404:
                            response.raise_for_status()
                        is_pdf = "application/pdf" in content_type or urlparse(final_url).path.casefold().endswith(".pdf")
                        if is_pdf:
                            if not self.settings.search_pdfs:
                                continue
                            page, contacts, general, scanned = parse_pdf(
                                response.content,
                                final_url,
                                self.settings.selected_categories,
                            )
                            page.depth = depth
                            outcome.pages.append(page)
                            outcome.contacts.extend(contacts)
                            outcome.general_contacts.extend(general)
                            outcome.pdfs_inspected += 1
                            if scanned:
                                outcome.scanned_pdfs.append(final_url)
                        else:
                            html = response.text
                            parsed = parse_html_page(
                                html,
                                final_url,
                                depth,
                                "httpx",
                                self.settings.selected_categories,
                            )
                            if self.settings.use_javascript and self._needs_javascript(parsed, html, final_url):
                                rendered = self._render_javascript(final_url)
                                if rendered:
                                    parsed = parse_html_page(
                                        rendered,
                                        final_url,
                                        depth,
                                        "Playwright",
                                        self.settings.selected_categories,
                                    )
                                    outcome.javascript_used = True
                            outcome.pages.append(parsed.page)
                            outcome.contacts.extend(parsed.contacts)
                            outcome.general_contacts.extend(parsed.general_contacts)
                            if depth < self.settings.max_depth:
                                for link in parsed.links:
                                    if link in discovered or not self._is_allowed_url(link, root_domain, allowed_hosts):
                                        continue
                                    priority = self._link_priority(link, depth + 1, parsed)
                                    if priority is None:
                                        continue
                                    discovered.add(link)
                                    counter += 1
                                    heapq.heappush(queue, (priority, depth + 1, counter, link))

                        outcome.relevant_pages = sum(1 for page in outcome.pages if page.relevant)
                        outcome.total_pages_discovered = max(len(discovered), len(outcome.pages))
                        live_contacts = []
                        seen_emails: set[str] = set()
                        for contact in outcome.contacts:
                            if contact.email.casefold() in seen_emails:
                                continue
                            seen_emails.add(contact.email.casefold())
                            live_contacts.append({
                                "name": contact.staff_name,
                                "role": contact.role,
                                "category": contact.role_category,
                                "credentials": contact.credentials,
                                "email": contact.email,
                                "source": contact.source_url,
                            })
                        for contact in outcome.general_contacts:
                            if contact.email.casefold() in seen_emails:
                                continue
                            seen_emails.add(contact.email.casefold())
                            live_contacts.append({
                                "name": contact.department_name,
                                "role": "General department contact",
                                "category": contact.role_category,
                                "credentials": "",
                                "email": contact.email,
                                "source": contact.source_url,
                            })
                        self.progress("crawling", {
                            "url": final_url,
                            "pages": len(outcome.pages),
                            "total_pages": outcome.total_pages_discovered,
                            "contacts": len(outcome.contacts) + len(outcome.general_contacts),
                            "live_contacts": live_contacts,
                        })
                    except Exception as exc:
                        outcome.errors.append(f"Could not process {url}: {type(exc).__name__}: {exc}")
            outcome.total_pages_discovered = max(len(discovered), len(outcome.pages))
            outcome.page_limit_reached = (
                self.settings.max_pages is not None
                and len(outcome.pages) >= self.settings.max_pages
                and bool(queue)
            )
        return outcome

    @staticmethod
    def _is_allowed_url(
        url: str,
        root_domain: str,
        allowed_hosts: set[str],
    ) -> bool:
        host = (urlparse(normalize_url(url)).hostname or "").casefold()
        same_district_domain = host == root_domain or host.endswith("." + root_domain)
        return registered_domain(url) == root_domain and (host in allowed_hosts or same_district_domain)

    def _link_priority(self, link: str, next_depth: int, parsed: ParsedHTML) -> int | None:
        """Return a heap score for useful links, or None when a link should not consume crawl budget."""
        if next_depth > self.settings.max_depth:
            return None
        if link in parsed.role_links:
            return -40 + next_depth
        if is_low_value_url(link):
            return None
        explicitly_relevant = link in parsed.support_links or is_support_topic_url(link)
        if explicitly_relevant:
            return -20 + next_depth
        if is_pdf_url(link):
            return None
        if next_depth <= 2 and is_bridge_url(link):
            return 2 + next_depth
        # A generic staff/directory page is useful as an entry point, but following
        # every employee profile beneath it recreates a full-site crawl. On deeper
        # pages, only role-labelled or student-support links are followed.
        if next_depth == 1 and (link in parsed.directory_links or is_directory_url(link)):
            return 8
        return None

    def _robots(self, client: httpx.Client, root: str) -> RobotFileParser:
        parsed = urlparse(root)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            response = self._get(client, robots_url)
            if response.status_code < 400:
                parser.parse(response.text.splitlines())
            else:
                parser.parse([])
        except httpx.HTTPError:
            parser.parse([])
        return parser

    def _sitemap_candidates(
        self,
        client: httpx.Client,
        root: str,
        robots: RobotFileParser,
        allowed_hosts: set[str] | None = None,
    ) -> list[str]:
        parsed = urlparse(root)
        url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
        if not robots.can_fetch(USER_AGENT, url):
            return []
        try:
            response = self._get(client, url)
            if response.status_code < 400 and len(response.content) < 8_000_000:
                root_domain = registered_domain(root)
                links = [
                    link for link in sitemap_links(response.text, url)
                    if same_domain(root, link)
                    and (not allowed_hosts or self._is_allowed_url(link, root_domain, allowed_hosts))
                ]
                support = [link for link in links if is_support_topic_url(link)]
                directories = [
                    link for link in links
                    if is_directory_url(link) and link not in support
                ]
                focused = support[:200] + directories[:25]
                return focused or links[:40]
        except httpx.HTTPError:
            pass
        return []

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def _get(self, client: httpx.Client, url: str) -> httpx.Response:
        cache = cache_path(url)
        cached = read_json_cache(cache)
        if cached and cached.get("url") == url:
            content = bytes.fromhex(cached["content_hex"]) if cached.get("content_hex") else cached.get("text", "").encode("utf-8")
            return httpx.Response(
                int(cached.get("status_code", 200)),
                headers=cached.get("headers", {}),
                content=content,
                request=httpx.Request("GET", url),
            )
        domain = registered_domain(url)
        with _DOMAIN_STATE_LOCK:
            rate_lock = _DOMAIN_RATE_LOCKS.setdefault(domain, threading.Lock())
            semaphore = _DOMAIN_SEMAPHORES.setdefault(domain, threading.BoundedSemaphore(2))
        with semaphore:
            with rate_lock:
                elapsed = time.monotonic() - _DOMAIN_LAST_REQUEST.get(domain, 0.0)
                if elapsed < self.settings.rate_limit_seconds:
                    time.sleep(self.settings.rate_limit_seconds - elapsed)
                _DOMAIN_LAST_REQUEST[domain] = time.monotonic()
            response = client.get(url)
        if response.status_code < 400 and len(response.content) <= 8_000_000:
            is_binary = "pdf" in response.headers.get("content-type", "").casefold()
            with _CACHE_WRITE_LOCK:
                write_json_cache(cache, {
                    "url": url,
                    "status_code": response.status_code,
                    "headers": {"content-type": response.headers.get("content-type", "")},
                    "content_hex": response.content.hex() if is_binary else "",
                    "text": "" if is_binary else response.text,
                    "checked_at": utc_now(),
                })
        return response

    @staticmethod
    def _needs_javascript(parsed: ParsedHTML, html: str, url: str) -> bool:
        likely_directory = is_priority_url(url)
        app_markers = any(marker in html.casefold() for marker in ("__next_data__", "ng-app", "id=\"root\"", "id=\"app\""))
        return (likely_directory or app_markers) and len(parsed.page.text) < 220 and not parsed.contacts

    @staticmethod
    def _is_public_staff_profile(html: str) -> bool:
        """Some Edlio sites return HTTP 404 for real, populated public staff profiles."""
        lowered = (html or "").casefold()
        return all(marker in lowered for marker in (
            'class="primary-info"',
            'class="user-name"',
            'class="position-user-page"',
        ))

    @staticmethod
    def _render_javascript(url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                launch_options: dict[str, object] = {"headless": True}
                configured_browser = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "").strip()
                system_browser = configured_browser or shutil.which("chromium") or shutil.which("chromium-browser")
                if system_browser:
                    launch_options["executable_path"] = system_browser
                if os.name != "nt":
                    launch_options["args"] = ["--no-sandbox", "--disable-dev-shm-usage"]
                browser = playwright.chromium.launch(**launch_options)
                page = browser.new_page(user_agent=USER_AGENT)
                page.goto(url, wait_until="networkidle", timeout=20_000)
                content = page.content()
                browser.close()
                return content
        except Exception:
            return ""
