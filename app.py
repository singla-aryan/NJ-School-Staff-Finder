from __future__ import annotations

from dataclasses import replace
from typing import Callable

import httpx
import streamlit as st

from src.exporters import contacts_dataframe, csv_bytes, excel_bytes, unresolved_dataframe
from src.job_manager import JobSnapshot, SchoolJobManager
from src.models import SchoolMatch, SchoolSearchResult, SearchSettings, WebsiteMatch
from src.nj_school_directory import NJSchoolDirectory
from src.result_cache import CachedSchoolResult, load_result, save_result
from src.result_processor import MAX_CONCURRENT_SCHOOLS, SchoolSearchService, process_website_override
from src.role_matcher import ROLE_CATEGORIES
from src.school_matcher import SchoolMatcher, confirm_school_match
from src.utilities import clean_school_lines, normalize_url, utc_now
from src.website_validator import validate_user_website

st.set_page_config(
    page_title="NJ School Student-Support Staff Finder",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .block-container {max-width: 1280px; padding-top: 2.1rem; padding-bottom: 4rem;}
    h1 {letter-spacing: -0.035em; color: #153b57;}
    [data-testid="stMetric"] {background: #f7fafc; border: 1px solid #e1e8ed; border-radius: .7rem; padding: .65rem .8rem;}
    .privacy-note {background:#f1f7f4; border-left:4px solid #28785b; padding:.8rem 1rem; border-radius:.35rem; color:#24463a;}
    .muted {color:#5e6d76; font-size:.94rem;}
    .contact-head {font-size:.78rem; font-weight:700; color:#667781; text-transform:uppercase; letter-spacing:.04em;}
    </style>
    """,
    unsafe_allow_html=True,
)


def initialize_state() -> None:
    defaults = {
        "results": {},
        "job_schools": [],
        "website_overrides": {},
        "pending_websites": {},
        "confirm_clear": False,
        "clear_mode": "clear",
        "school_input": "",
        "reset_input": False,
        "cached_result_offers": {},
        "cached_result_dates": {},
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    if "job_manager" not in st.session_state:
        st.session_state.job_manager = SchoolJobManager(MAX_CONCURRENT_SCHOOLS)


@st.cache_resource(show_spinner=False)
def load_directory() -> tuple[NJSchoolDirectory, list]:
    provider = NJSchoolDirectory()
    return provider, provider.load()


def default_settings() -> SearchSettings:
    """Search every supported source and role using fixed, responsible limits."""
    return SearchSettings(
        selected_categories=list(ROLE_CATEGORIES),
        include_general_contacts=True,
        use_javascript=True,
        search_pdfs=True,
        max_pages=None,
        max_depth=4,
        timeout=15.0,
        show_review_contacts=True,
    )


def store_fresh_result(name: str, result: SchoolSearchResult) -> None:
    """Store a completed result in the session and persistent result cache."""
    st.session_state.results[name] = result
    st.session_state.cached_result_dates.pop(name, None)
    save_result(result)


def result_for_input(cached: CachedSchoolResult, name: str) -> SchoolSearchResult:
    """Keep a cached result's evidence while adapting its input label to this request."""
    school_input = replace(cached.result.school_match.input_school, raw_name=name)
    school_match = replace(cached.result.school_match, input_school=school_input)
    return replace(cached.result, input_school_name=name, school_match=school_match)


def progress_callback_factory(status_box, detail_box, contacts_box=None):
    contacts_box = contacts_box or st.empty()

    def callback(stage: str, data: dict) -> None:
        labels = {
            "identifying": "School identified",
            "website": "Website discovery complete",
            "crawling": "Searching the official website",
        }
        status_box.info(labels.get(stage, stage.title()))
        if stage == "crawling":
            pages = data.get("pages", 0)
            total_pages = max(data.get("total_pages", 0), pages)
            detail_box.caption(
                f"Pages searched: {pages}/{total_pages} · Contacts found so far: {data.get('contacts', 0)} · {data.get('url', '')}"
            )
            live_contacts = data.get("live_contacts", [])
            if live_contacts:
                lines = ["#### Contacts found so far"]
                for contact in live_contacts:
                    lines.append(
                        f"- **{contact['name']}** — {contact['role']} — `{contact['email']}` — [Source]({contact['source']})"
                    )
                contacts_box.markdown("\n".join(lines))
            else:
                contacts_box.info("No qualifying public contacts found yet. The search is still running.")
        elif stage == "website":
            detail_box.caption(f"Website status: {data.get('status', '')} · {data.get('url', '')}")
        else:
            detail_box.caption(f"School-match status: {data.get('status', '')}")
    return callback


def recoverable_result(name: str, match: SchoolMatch, exc: Exception) -> SchoolSearchResult:
    return SchoolSearchResult(
        input_school_name=name,
        school_match=match,
        status="Failed with recoverable error",
        errors=["This school could not be completed. You can retry it without restarting the batch."],
        debug_details=[f"{type(exc).__name__}: {exc}"],
        checked_at=utc_now(),
    )


def run_one(
    name: str,
    settings: SearchSettings,
    match: SchoolMatch | None = None,
    website_match: WebsiteMatch | None = None,
    broad: bool = False,
    status_box=None,
    detail_box=None,
    contacts_box=None,
) -> None:
    provider, records = load_directory()
    status_box = status_box or st.empty()
    detail_box = detail_box or st.empty()
    match = match or SchoolMatcher(records).match(name)
    if match.status == "Not Found":
        status_box.info("Checking official NJDOE school records…")
        official_candidate = provider.resolve_school(name)
        if official_candidate:
            is_official = "nj.gov" in official_candidate.source_url.casefold()
            match = replace(
                match,
                status="Verified" if is_official else "Likely",
                confidence="Verified" if is_official else "Likely",
                score=100.0 if is_official else 95.0,
                candidate=official_candidate,
                alternatives=[],
                reason=(
                    "Exact school identified from an official NJDOE performance report."
                    if is_official
                    else "Exact high-school name identified from the supplemental New Jersey list."
                ),
            )
    callback = progress_callback_factory(status_box, detail_box, contacts_box)
    try:
        result = SchoolSearchService(settings, callback).search_match(match, website_match=website_match, broad=broad)
    except Exception as exc:
        result = recoverable_result(name, match, exc)
    store_fresh_result(name, result)


def _prepare_batch_match(name: str, matcher: SchoolMatcher, provider: NJSchoolDirectory) -> SchoolMatch:
    match = matcher.match(name)
    if match.status != "Not Found":
        return match
    official_candidate = provider.resolve_school(name)
    if not official_candidate:
        return match
    is_official = "nj.gov" in official_candidate.source_url.casefold()
    return replace(
        match,
        status="Verified" if is_official else "Likely",
        confidence="Verified" if is_official else "Likely",
        score=100.0 if is_official else 95.0,
        candidate=official_candidate,
        alternatives=[],
        reason=(
            "Exact school identified from an official NJDOE performance report."
            if is_official
            else "Exact high-school name identified from the supplemental New Jersey list."
        ),
    )


def _search_school_job(
    name: str,
    match: SchoolMatch,
    settings: SearchSettings,
    report: Callable[[str, dict], None],
) -> SchoolSearchResult:
    try:
        return SchoolSearchService(settings, report).search_match(match)
    except Exception as exc:
        return recoverable_result(name, match, exc)


def run_batch(names: list[str], settings: SearchSettings) -> list[str]:
    """Append new schools to the persistent queue without disturbing active work."""
    manager: SchoolJobManager = st.session_state.job_manager
    provider, records = load_directory()
    matcher = SchoolMatcher(records)
    queued: list[str] = []
    for name in names:
        if name not in st.session_state.job_schools:
            st.session_state.job_schools.append(name)
        existing = st.session_state.results.get(name)
        if existing and (existing.status.startswith("Completed") or existing.status == "Website provided by user"):
            continue
        if manager.contains(name):
            continue
        match = _prepare_batch_match(name, matcher, provider)
        submitted = manager.submit(
            name,
            lambda progress, school_name=name, school_match=match: _search_school_job(
                school_name,
                school_match,
                settings,
                progress,
            ),
        )
        if submitted:
            queued.append(name)
    return queued


def prepare_search_requests(names: list[str], settings: SearchSettings) -> tuple[list[str], list[str]]:
    """Offer saved results when available and queue only schools without an offer."""
    manager: SchoolJobManager = st.session_state.job_manager
    fresh_names: list[str] = []
    offered: list[str] = []
    offers: dict[str, CachedSchoolResult] = st.session_state.cached_result_offers
    for name in names:
        if name in st.session_state.results or manager.contains(name) or name in offers:
            continue
        cached = load_result(name)
        if cached is None:
            fresh_names.append(name)
            continue
        offers[name] = cached
        if name not in st.session_state.job_schools:
            st.session_state.job_schools.append(name)
        offered.append(name)
    return run_batch(fresh_names, settings), offered


def render_cached_result_offers(settings: SearchSettings) -> None:
    offers: dict[str, CachedSchoolResult] = st.session_state.cached_result_offers
    if not offers:
        return
    st.subheader("Saved searches available")
    for name, cached in list(offers.items()):
        school = cached.result.school
        school_name = school.canonical_name if school else name
        with st.container(border=True):
            st.info(
                f"I already have saved results for **{school_name}** from **{cached.date_label}**. "
                "Would you like to see those results or search the website again?"
            )
            saved_column, redo_column = st.columns(2)
            if saved_column.button(
                "Show Saved Results",
                key=f"use-cached-result-{name}",
                type="primary",
                use_container_width=True,
            ):
                st.session_state.results[name] = result_for_input(cached, name)
                st.session_state.cached_result_dates[name] = cached.date_label
                offers.pop(name, None)
                st.rerun(scope="app")
            if redo_column.button(
                "Search Website Again",
                key=f"refresh-cached-result-{name}",
                use_container_width=True,
            ):
                offers.pop(name, None)
                st.session_state.cached_result_dates.pop(name, None)
                run_batch([name], settings)
                st.rerun(scope="app")


def validate_override(result: SchoolSearchResult, value: str, settings: SearchSettings) -> None:
    if not result.school:
        st.error("Confirm the school before providing its website.")
        return
    url = normalize_url(value)
    if not url:
        st.error("Enter a valid public http or https website.")
        return
    html = ""
    final_url = url
    try:
        with httpx.Client(timeout=settings.timeout, follow_redirects=True, max_redirects=5) as client:
            response = client.get(url, headers={"User-Agent": "NJSchoolStudentSupportFinder/1.0"})
            if response.status_code < 400:
                final_url = str(response.url)
                if "html" in response.headers.get("content-type", "").casefold() or not response.headers.get("content-type"):
                    html = response.text
    except httpx.HTTPError:
        pass
    match = validate_user_website(final_url, result.school, html)
    st.session_state.website_overrides[result.input_school_name] = url
    if match.status in {"Verified", "Likely"}:
        updated = process_website_override(result, match, settings)
        store_fresh_result(result.input_school_name, updated)
        st.rerun()
    elif match.status == "Needs Confirmation":
        st.session_state.pending_websites[result.input_school_name] = match
        st.warning(match.reason)
    else:
        st.error(match.reason)


def confirm_pending_website(result: SchoolSearchResult, settings: SearchSettings) -> None:
    match = st.session_state.pending_websites.get(result.input_school_name)
    if not match:
        return
    approved = replace(
        match,
        status="Likely",
        confidence="Likely",
        source="User-provided website (confirmed)",
        reason=match.reason + " Confirmed by the user.",
    )
    updated = process_website_override(result, approved, settings)
    store_fresh_result(result.input_school_name, updated)
    st.session_state.pending_websites.pop(result.input_school_name, None)
    st.rerun()


def render_contact(contact) -> None:
    columns = st.columns([1.0, 1.2, 0.95, 0.65, 1.1, 0.42])
    columns[0].write(contact.staff_name)
    columns[1].write(contact.role)
    columns[2].write(contact.school or "District-wide")
    columns[3].write(contact.credentials or "—")
    columns[4].code(contact.email, language=None)
    columns[5].link_button("Source", contact.source_url, use_container_width=True)
    if contact.evidence_snippet:
        st.caption(f"Evidence: {contact.evidence_snippet}")
    if len(contact.source_urls) > 1:
        with st.expander(f"{len(contact.source_urls)} source pages", expanded=False):
            for index, url in enumerate(contact.source_urls, start=1):
                st.markdown(f"[{index}. Source]({url})")


def render_general_contact(contact) -> None:
    columns = st.columns([1.2, 1.2, 0.45])
    columns[0].write(contact.department_name)
    columns[1].code(contact.email, language=None)
    columns[2].link_button("Source", contact.source_url, use_container_width=True)


def render_resolution_controls(result: SchoolSearchResult, settings: SearchSettings) -> None:
    name = result.input_school_name
    if result.status == "School match needs review":
        st.warning("School match needs review")
        alternatives = result.school_match.alternatives
        if alternatives:
            choice = st.selectbox(
                "Choose the correct New Jersey school",
                options=range(len(alternatives)),
                format_func=lambda index: alternatives[index].label,
                key=f"school-choice-{name}",
            )
            if st.button("Confirm School", key=f"confirm-school-{name}", type="primary"):
                confirmed = confirm_school_match(result.school_match, alternatives[choice])
                run_one(name, settings, match=confirmed)
                st.rerun()
        else:
            st.write("No credible NJDOE match was found. Check the spelling, or refresh the official directory and retry.")
        if st.button("Retry School Identification", key=f"retry-id-{name}"):
            load_directory.clear()
            run_one(name, settings)
            st.rerun()
        return

    if result.status == "Website needs confirmation" and result.website_match:
        st.warning("Website needs confirmation")
        st.write(result.website_match.reason)
        if result.website_match.url:
            st.markdown(f"Candidate: [{result.website_match.url}]({result.website_match.url})")
            if st.button("Confirm This Website and Search", key=f"confirm-auto-site-{name}", type="primary"):
                approved = replace(result.website_match, status="Likely", confidence="Likely", reason=result.website_match.reason + " Confirmed by the user.")
                run_one(name, settings, match=result.school_match, website_match=approved)
                st.rerun()

    if result.status == "Website not found":
        st.warning("Website not confidently found")
        school = result.school
        st.write(f"**School:** {school.canonical_name if school else name}")
        st.write(f"**District:** {school.district_name if school else 'Not identified'}")
        st.write(f"**Municipality:** {school.municipality if school and school.municipality else 'Not available'}")
        st.write(f"**County:** {school.county if school and school.county else 'Not available'}")
        with st.expander("Discovery attempts made", expanded=False):
            for attempt in result.discovery_attempts:
                st.write(f"• {attempt}")
        with st.expander("Potential candidates considered", expanded=False):
            candidates = result.website_match.candidates if result.website_match else []
            if candidates:
                for candidate in candidates:
                    st.markdown(f"[{candidate.title or candidate.url}]({candidate.url}) — {candidate.source}")
            else:
                st.write("No credible candidate domains were returned.")
        st.write("The app searched NJDOE records and public search results but could not confidently identify the official website. Paste the official school or district website below to continue.")

    if result.status in {"Website not found", "Website needs confirmation", "Completed — no public contacts found", "Failed with recoverable error", "Website blocked crawling", "Partial result"}:
        override = st.text_input(
            "Official school or district website",
            value=st.session_state.website_overrides.get(name, ""),
            key=f"override-{name}",
            placeholder="https://www.district.org/",
        )
        col1, col2 = st.columns(2)
        if col1.button("Use This Website and Search Again", key=f"use-site-{name}", type="primary", use_container_width=True):
            validate_override(result, override, settings)
        if col2.button("Retry Automatic Website Search", key=f"retry-auto-{name}", use_container_width=True):
            run_one(name, settings, match=result.school_match, broad=True)
            st.rerun()

    if name in st.session_state.pending_websites:
        pending = st.session_state.pending_websites[name]
        st.warning(f"The provided site could not be conclusively linked to the school: {pending.url}")
        st.write(pending.reason)
        if st.button("I Confirm This Is the Official Website", key=f"approve-override-{name}", type="primary"):
            confirm_pending_website(result, settings)


def render_result(result: SchoolSearchResult, settings: SearchSettings) -> None:
    school = result.school
    title = school.canonical_name if school else result.input_school_name
    district = school.district_name if school and school.district_name else "District not identified"
    with st.container(border=True):
        st.subheader(f"{title} — {district}")
        cached_date = st.session_state.cached_result_dates.get(result.input_school_name)
        if cached_date:
            st.info(
                f"Showing the saved result from {cached_date}. "
                "Use **Retry This School** below whenever you want a fresh website search."
            )
        if title != result.input_school_name:
            st.caption(f"Input: {result.input_school_name}")
        metrics = st.columns(4)
        metrics[0].metric("School match", result.school_match.confidence, f"{result.school_match.score:.0f}%")
        metrics[1].metric("Website confidence", result.website_match.confidence if result.website_match else "Pending")
        total_pages = max(result.total_pages_discovered, result.pages_searched)
        metrics[2].metric(
            "Pages searched",
            f"{result.pages_searched}/{total_pages}",
            help="Pages processed / relevant or navigational internal pages selected by the focused crawler.",
        )
        metrics[3].metric("Verified contacts", len(result.contacts) + len(result.general_contacts))
        detail_parts = []
        if school and school.municipality:
            detail_parts.append(school.municipality)
        if school and school.county:
            detail_parts.append(f"{school.county} County")
        detail_parts.append(result.status)
        st.caption(" · ".join(detail_parts))
        if result.website_match and result.website_match.url:
            st.markdown(f"Official website searched: [{result.website_match.url}]({result.website_match.url})")

        if result.contacts:
            st.markdown("### Publicly listed professionals")
            for category in ROLE_CATEGORIES:
                category_contacts = [contact for contact in result.contacts if contact.role_category == category]
                if not category_contacts:
                    continue
                st.markdown(f"#### {category}")
                header = st.columns([1.0, 1.2, 0.95, 0.65, 1.1, 0.42])
                for column, label in zip(header, ("Staff name", "Exact role", "School", "Credentials", "Email", "Source")):
                    column.markdown(f"<span class='contact-head'>{label}</span>", unsafe_allow_html=True)
                for contact in category_contacts:
                    render_contact(contact)
        elif result.status in {"Completed — no public contacts found", "Website provided by user"} and not result.general_contacts:
            st.info("No publicly listed qualifying staff emails were found.")

        if result.general_contacts:
            st.markdown("#### General Department Contacts")
            for contact in result.general_contacts:
                render_general_contact(contact)

        if settings.show_review_contacts and result.review_contacts:
            with st.expander("Possible Contacts Requiring Review", expanded=False):
                st.write(
                    "These titles may be relevant, but the source does not clearly connect them to mental-health, "
                    "psychological, social-work, or counseling services. They are excluded from verified results."
                )
                header = st.columns([1.0, 1.2, 0.95, 0.65, 1.1, 0.42])
                for column, label in zip(header, ("Staff name", "Exact role", "School", "Credentials", "Email", "Source")):
                    column.markdown(f"<span class='contact-head'>{label}</span>", unsafe_allow_html=True)
                for contact in result.review_contacts:
                    render_contact(contact)

        if result.pages_searched:
            st.caption(
                f"Relevant pages: {result.relevant_pages} · PDFs inspected: {result.pdfs_inspected} · "
                f"JavaScript fallback: {'used' if result.javascript_used else 'not used'} · "
                f"Crawl restrictions: {'encountered' if result.crawl_restricted else 'none detected'}"
            )
        if result.page_limit_reached:
            st.warning(
                f"The focused search examined its {result.pages_searched} most promising pages. "
                f"It found {result.total_pages_discovered} possible support or navigation pages in total; "
                "lower-priority pages were left unsearched, and unrelated archives were skipped."
            )
        if result.scanned_pdfs:
            with st.expander("Scanned PDF found — manual review may be required", expanded=False):
                for url in result.scanned_pdfs:
                    st.markdown(f"[PDF source]({url})")
        if result.errors:
            st.warning("Some pages could not be searched. The completed evidence, if any, is preserved.")
        if result.errors or result.debug_details:
            with st.expander("Technical details", expanded=False):
                for error in [*result.errors, *result.debug_details]:
                    st.code(error, language=None)

        render_resolution_controls(result, settings)
        if result.status not in {"Pending", "School match needs review", "Website not found", "Website needs confirmation"}:
            if st.button("Retry This School", key=f"retry-school-{result.input_school_name}"):
                run_one(result.input_school_name, settings, match=result.school_match, broad=True)
                st.rerun()


def render_unresolved_summary(results: list[SchoolSearchResult], settings: SearchSettings) -> None:
    unresolved_statuses = {"School match needs review", "Website needs confirmation", "Website not found", "Failed with recoverable error", "Website blocked crawling"}
    unresolved = [result for result in results if result.status in unresolved_statuses]
    if not unresolved:
        return
    with st.container(border=True):
        st.subheader("Schools Needing Website Help")
        st.write("Completed schools remain saved while you resolve these items.")
        for result in unresolved:
            st.write(f"• **{result.input_school_name}** — {result.status}")
        if st.button("Retry All Unresolved Schools", type="primary", use_container_width=True):
            for result in unresolved:
                run_one(result.input_school_name, settings, match=result.school_match, broad=True)
            st.rerun()


def render_exports(results: list[SchoolSearchResult]) -> None:
    if not results:
        return
    st.subheader("Downloads")
    all_contacts = contacts_dataframe(results)
    verified = contacts_dataframe(results, verified_only=True)
    unresolved = unresolved_dataframe(results)
    columns = st.columns(4)
    columns[0].download_button(
        "Download All Results as CSV", csv_bytes(all_contacts), "nj-school-staff-all.csv", "text/csv", use_container_width=True,
    )
    columns[1].download_button(
        "Download All Results as Excel", excel_bytes(results), "nj-school-staff-results.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True,
    )
    columns[2].download_button(
        "Download Verified Contacts Only as CSV", csv_bytes(verified), "nj-school-staff-verified.csv", "text/csv", use_container_width=True,
    )
    columns[3].download_button(
        "Download Unresolved Schools as CSV", csv_bytes(unresolved), "nj-school-unresolved.csv", "text/csv", use_container_width=True,
    )


def render_clear_controls() -> None:
    results = st.session_state.results
    manager: SchoolJobManager = st.session_state.job_manager
    if not results and not manager.has_active():
        return
    st.divider()
    col1, col2 = st.columns(2)
    if col1.button("Clear All Results", use_container_width=True):
        st.session_state.confirm_clear = True
        st.session_state.clear_mode = "clear"
    if col2.button("Start a New Search", use_container_width=True):
        st.session_state.confirm_clear = True
        st.session_state.clear_mode = "new"
    if st.session_state.confirm_clear:
        st.warning("This will remove all results and cancel queued searches. Active network requests will be discarded. Continue?")
        yes, no = st.columns(2)
        if yes.button("Yes, clear all", type="primary", use_container_width=True):
            manager.shutdown()
            st.session_state.job_manager = SchoolJobManager(MAX_CONCURRENT_SCHOOLS)
            st.session_state.results = {}
            st.session_state.job_schools = []
            st.session_state.website_overrides = {}
            st.session_state.pending_websites = {}
            st.session_state.cached_result_offers = {}
            st.session_state.cached_result_dates = {}
            if st.session_state.clear_mode == "new":
                st.session_state.reset_input = True
            st.session_state.confirm_clear = False
            st.rerun()
        if no.button("Cancel", use_container_width=True):
            st.session_state.confirm_clear = False
            st.rerun()


def _ordered_results() -> list[SchoolSearchResult]:
    results = [
        st.session_state.results[name]
        for name in st.session_state.job_schools
        if name in st.session_state.results
    ]
    for result in st.session_state.results.values():
        if result not in results:
            results.append(result)
    return results


def _render_job_snapshot(snapshot: JobSnapshot) -> None:
    card = st.container(border=True)
    card.markdown(f"**{snapshot.name}**")
    result = st.session_state.results.get(snapshot.name)
    if snapshot.state == "finished" and result:
        card.success(f"Finished: {result.status}")
        card.caption(
            f"Pages searched: {result.pages_searched}/{max(result.total_pages_discovered, result.pages_searched)} "
            f"- Verified contacts: {len(result.contacts) + len(result.general_contacts)}"
        )
        return
    if snapshot.state == "queued":
        card.info("Queued - waiting for an available school-search slot")
        return

    labels = {
        "identifying": "School identified",
        "website": "Website discovery complete",
        "crawling": "Searching the official website",
    }
    card.info(labels.get(snapshot.stage, "Search running"))
    data = snapshot.data
    if snapshot.stage == "crawling":
        pages = data.get("pages", 0)
        total_pages = max(data.get("total_pages", 0), pages)
        card.caption(
            f"Pages searched: {pages}/{total_pages} · Contacts found so far: {data.get('contacts', 0)} · "
            f"{data.get('url', '')}"
        )
        live_contacts = data.get("live_contacts", [])
        if live_contacts:
            lines = ["#### Contacts found so far"]
            for contact in live_contacts:
                lines.append(
                    f"- **{contact['name']}** — {contact['role']} — `{contact['email']}` — "
                    f"[Source]({contact['source']})"
                )
            card.markdown("\n".join(lines))
        else:
            card.caption("No qualifying public contacts found yet. The search is still running.")
    elif snapshot.stage == "website":
        card.caption(f"Website status: {data.get('status', '')} · {data.get('url', '')}")
    elif snapshot.stage == "identifying":
        card.caption(f"School-match status: {data.get('status', '')}")


def render_dynamic_content(settings: SearchSettings) -> None:
    manager: SchoolJobManager = st.session_state.job_manager
    completed_jobs, snapshots = manager.poll()
    for completed in completed_jobs:
        if completed.result is not None:
            store_fresh_result(completed.name, completed.result)

    render_cached_result_offers(settings)

    progress_names = [
        name
        for name in st.session_state.job_schools
        if name not in st.session_state.cached_result_offers
    ]
    if progress_names:
        progress_area = st.container(border=True)
        progress_area.subheader("Search progress")
        total = len(progress_names)
        finished_names = {
            snapshot.name for snapshot in snapshots if snapshot.state == "finished"
        } | set(st.session_state.results)
        finished = sum(name in finished_names for name in progress_names)
        progress_area.progress(
            min(1.0, finished / total) if total else 0.0,
            text=f"Overall progress: {finished} of {total} schools - up to {MAX_CONCURRENT_SCHOOLS} at once",
        )
        _provider, _records = load_directory()
        progress_area.caption(_provider.last_status)
        with progress_area:
            for snapshot in snapshots:
                _render_job_snapshot(snapshot)
        if manager.has_active():
            progress_area.info("Searches are running in the background. You can add more schools above at any time.")
        else:
            progress_area.success("All queued schools have finished.")

    results = _ordered_results()
    if results:
        st.divider()
        render_unresolved_summary(results, settings)
        st.header("Results by school")
        for result in results:
            render_result(result, settings)
        render_exports(results)
    render_clear_controls()

    if completed_jobs and not manager.has_active():
        st.rerun(scope="app")


initialize_state()
if st.session_state.reset_input:
    st.session_state.school_input = ""
    st.session_state.reset_input = False
settings = default_settings()

st.title("NJ School Student-Support Staff Finder")
st.write(
    "Enter one New Jersey school per line. The app will identify the school and district, find the official website, "
    "and search for publicly listed therapists and clinicians, school psychologists, social workers, student-assistance "
    "staff, school counselors, and relevant mental-health or student-services leaders."
)
st.caption(
    "Clinical professionals and school counselors are categorized separately. Ambiguous support titles are held for review. "
    "Relevant staff elsewhere in the same district are included and labeled with their actual school. "
    "You can add more schools while searches are running."
)
st.markdown(
    "<div class='privacy-note'>This tool searches publicly accessible official New Jersey school and district websites for professional contact information. "
    "It does not guess email addresses, access private systems, bypass restrictions, or use data-broker information.</div>",
    unsafe_allow_html=True,
)
st.write("")
school_text = st.text_area(
    "New Jersey schools — one school per line",
    key="school_input",
    height=190,
    placeholder="Princeton High School\nWest Windsor-Plainsboro High School South\nEdison High School",
)
manager: SchoolJobManager = st.session_state.job_manager
button_label = "Add Schools to Current Search" if manager.has_active() else "Find Public Staff Emails"
if st.button(button_label, key="queue-school-searches", type="primary", use_container_width=True):
    names = clean_school_lines(school_text)
    if not names:
        st.error("Enter at least one New Jersey school name.")
    else:
        queued, offered = prepare_search_requests(names, settings)
        if offered:
            st.info(
                f"Found saved results for {len(offered)} school{'s' if len(offered) != 1 else ''}. "
                "Choose whether to use them or search again below."
            )
        if queued:
            action = "Added" if manager.has_active() else "Queued"
            st.success(
                f"{action} {len(queued)} school{'s' if len(queued) != 1 else ''}. "
                "Any searches already running will continue."
            )
        elif not offered:
            st.info("Those schools are already queued or completed.")

refresh_interval = 0.75 if manager.has_active() else None
dynamic_fragment = st.fragment(render_dynamic_content, run_every=refresh_interval)
dynamic_fragment(settings)
