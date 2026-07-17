from __future__ import annotations

import re
from email.utils import parseaddr
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag

from .models import GeneralContact, StaffContact
from .role_matcher import (
    SCHOOL_COUNSELING_CATEGORY,
    detect_role,
    extract_credentials,
    is_general_department_context,
    strip_credentials,
)
from .utilities import decode_cloudflare_email, safe_snippet, utc_now

EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Z0-9][A-Z0-9._%+'-]{0,63}@[A-Z0-9.-]+\.[A-Z]{2,24})(?![\w.-])", re.I)
OBFUSCATED_RE = re.compile(
    r"\b([A-Z0-9][A-Z0-9._%+'-]{0,63})\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\s+at\s+)\s*"
    r"([A-Z0-9-]+(?:\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\s+dot\s+)\s*[A-Z0-9-]+)+)\b",
    re.I,
)
INCOMPLETE_MARKERS = ("…", "***", "xxxxx", "[hidden]", "[redacted]")
GENERAL_LOCAL_PARTS = {
    "counseling", "counselling", "guidance", "studentservices", "student.services",
    "pupilservices", "pupil.services", "wellness", "supportservices", "cst", "sac",
}
PLACEHOLDER_LOCAL_PARTS = {
    "first.last", "firstname.lastname", "firstnamelastname", "firstinitiallastname",
    "name", "username", "user", "employee", "email", "yourname", "john.doe", "jane.doe",
}
PLACEHOLDER_DOMAINS = {"example.com", "example.org", "example.net", "domain.com", "school.org"}
NON_PERSON_NAMES = {
    "contact us", "current students", "for current students", "school counseling",
    "counseling office", "site feedback", "staff directory",
    "school anti-bullying specialists",
}


def normalize_email(value: str) -> str:
    candidate = unquote(value or "").strip().strip("<>[](){}.,;:'\"").lower()
    if candidate.startswith("mailto:"):
        candidate = candidate[7:].split("?", 1)[0]
    _, parsed = parseaddr(candidate)
    candidate = parsed or candidate
    if any(marker in candidate for marker in INCOMPLETE_MARKERS):
        return ""
    if not EMAIL_RE.fullmatch(candidate) or candidate.count("@") != 1:
        return ""
    local, domain = candidate.rsplit("@", 1)
    if len(candidate) > 254 or len(local) > 64 or ".." in candidate or domain.startswith("-"):
        return ""
    if local.casefold() in PLACEHOLDER_LOCAL_PARTS or domain.casefold() in PLACEHOLDER_DOMAINS:
        return ""
    return candidate


def extract_email_evidence(html: str) -> list[tuple[str, str, str, Tag | None]]:
    soup = BeautifulSoup(html or "", "lxml")
    evidence: list[tuple[str, str, str, Tag | None]] = []
    for link in soup.select("a[href^='mailto:' i]"):
        email = normalize_email(link.get("href", ""))
        if email:
            evidence.append((email, "mailto link", link.get_text(" ", strip=True), link))
    for protected in soup.select("[data-cfemail]"):
        email = normalize_email(decode_cloudflare_email(protected.get("data-cfemail", "")))
        if email:
            evidence.append((email, "public structured data", protected.get_text(" ", strip=True), protected))
    for script in soup.select("script[type='application/ld+json'], script[type='application/json']"):
        structured = script.string or script.get_text(" ", strip=True)
        for match in EMAIL_RE.finditer(structured):
            email = normalize_email(match.group(1))
            if email:
                evidence.append((email, "public structured data", structured, None))
    visible = soup.get_text("\n", strip=True)
    for match in EMAIL_RE.finditer(visible):
        email = normalize_email(match.group(1))
        if email:
            node = soup.find(string=lambda value: bool(value and email.casefold() in value.casefold()))
            evidence.append((email, "visible text", match.group(0), node.parent if node else None))
    for match in OBFUSCATED_RE.finditer(visible):
        domain = re.sub(r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\s+dot\s+)\s*", ".", match.group(2), flags=re.I)
        email = normalize_email(f"{match.group(1)}@{domain}")
        if email:
            node = soup.find(string=lambda value: bool(value and match.group(1).casefold() in value.casefold()))
            evidence.append((email, "public obfuscated text", match.group(0), node.parent if node else None))
    unique: dict[str, tuple[str, str, str, Tag | None]] = {}
    priority = {"mailto link": 4, "public structured data": 3, "visible text": 2, "public obfuscated text": 1}
    for item in evidence:
        if item[0] not in unique or priority[item[1]] > priority[unique[item[0]][1]]:
            unique[item[0]] = item
    return list(unique.values())


def _looks_like_named_context(context: str, email: str) -> bool:
    cleaned = re.sub(re.escape(email), " ", context, flags=re.I) if email else context
    for segment in re.split(r"\s*[|\n•·–—:{}\[\],\"]\s*", cleaned[:500]):
        value = re.sub(r"\s+", " ", segment).strip(" ,-;")
        if value.casefold() in NON_PERSON_NAMES or value.isupper():
            continue
        if re.fullmatch(r"(?:Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Mx\.?)?\s*[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’.-]+){1,3}", value):
            return True
    return False


def _context_for_node(node: Tag | None, email: str, fallback: str, roster_role: str = "") -> str:
    if node is None:
        return fallback
    current: Tag | None = node
    roster_context = ""
    role_context = ""
    for _ in range(7):
        if current is None:
            break
        text = current.get_text(" | ", strip=True)
        email_count = len(EMAIL_RE.findall(text))
        has_role = (
            10 <= len(text) <= 3000
            and email_count <= 3
            and current.name in {"li", "tr", "article", "section", "div", "dd", "p"}
            and detect_role(text) is not None
        )
        if has_role:
            if _looks_like_named_context(text, email):
                return text
            if not role_context:
                role_context = text
        if (
            roster_role
            and not roster_context
            and 10 <= len(text) <= 3000
            and email_count <= 3
            and current.name in {"li", "tr", "article", "section", "div"}
            and _looks_like_named_context(text, email)
        ):
            roster_context = text
        current = current.parent if isinstance(current.parent, Tag) else None
    return role_context or roster_context or node.get_text(" | ", strip=True) or fallback


def _extract_name(context: str, email: str, role: str) -> str:
    cleaned = re.sub(re.escape(email), " ", context, flags=re.I)
    cleaned = re.sub(re.escape(role), " ", cleaned, flags=re.I)
    cleaned = strip_credentials(cleaned)
    self_identified = re.search(
        r"\b(?:My|my) name is\s+(?:Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Mx\.?)?\s*"
        r"([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’.-]+){1,3})",
        cleaned,
    )
    if self_identified:
        return self_identified.group(1).strip(" .,-;")
    candidates = re.split(r"\s*[|\n•·–—:{},\[\]\"]\s*", cleaned)
    name_re = re.compile(r"^(?:Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Mx\.?)?\s*([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’.-]+){1,3})$")
    for candidate in candidates:
        value = re.sub(r"\s+", " ", candidate).strip(" ,-;")
        lowered = value.casefold()
        if (
            lowered in NON_PERSON_NAMES
            or value.isupper()
            or lowered.startswith(("at ", "for "))
            or lowered.endswith((" school", " counselor", " counselors", " specialists", " staff"))
        ):
            continue
        match = name_re.match(value)
        if match and not is_general_department_context(value):
            return re.sub(r"^(?:Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Mx\.?)\s+", "", value).strip()
    return ""


def _extract_exact_role(context: str, email: str, matched_role: str) -> str:
    cleaned = re.sub(re.escape(email), " ", context, flags=re.I)
    candidates = re.split(r"\s*[|\n•·–—;{}\[\]\"]\s*", cleaned)
    title_markers = (
        "counsel", "therap", "clinician", "psycholog", "social worker", "coordinator",
        "specialist", "director", "supervisor", "chair", "case manager", "child study",
        "pupil services", "student services", "wellness",
    )
    for candidate in candidates:
        value = re.sub(r"\s+", " ", candidate).strip(" ,-:")
        segment_role = detect_role(value)
        if not segment_role or len(value) > 160:
            continue
        if extract_credentials(value) and not any(marker in value.casefold() for marker in title_markers):
            continue
        value = re.sub(r"^(?:position|job title|title|role)\s*:\s*", "", value, flags=re.I)
        match = re.search(re.escape(segment_role.role), value, re.I)
        if match and match.start() > 0:
            prefix = value[:match.start()].strip(" ,-:")
            if re.fullmatch(r"(?:Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Mx\.?)?\s*[A-Z][A-Za-z'â€™.-]+(?:\s+[A-Z][A-Za-z'â€™.-]+){1,3}", prefix):
                value = value[match.start():].strip(" ,-:")
        return value
    return matched_role


def _page_roster_role(
    soup: BeautifulSoup,
    source_url: str,
    page_title: str,
    selected_categories: list[str] | None,
):
    headings = " ".join(
        heading.get_text(" ", strip=True)
        for heading in soup.select("h1, h2")[:12]
    )
    page_identity = f"{page_title} {headings} {source_url}".casefold()
    counselor_roster_markers = (
        "meet our counselors", "meet the counselors", "meet your counselors",
        "school counselor directory", "school counselors directory",
    )
    if any(marker in page_identity for marker in counselor_roster_markers):
        return detect_role("School Counselor", selected_categories)
    return None


def _panel_label_for_node(soup: BeautifulSoup, node: Tag | None) -> str:
    """Recover a visible tab/accordion person's name linked to a profile panel."""
    current = node
    for _ in range(8):
        if current is None:
            break
        panel_id = current.get("id", "")
        if panel_id:
            for control in soup.select("[aria-controls]"):
                if control.get("aria-controls") == panel_id:
                    label = control.get_text(" ", strip=True)
                    if _looks_like_named_context(label, ""):
                        return label
        current = current.parent if isinstance(current.parent, Tag) else None
    return ""


def _name_matches_email(name: str, email: str) -> bool:
    local = re.sub(r"[^a-z]", "", email.split("@", 1)[0].casefold())
    tokens = [re.sub(r"[^a-z]", "", token.casefold()) for token in name.split()]
    tokens = [token for token in tokens if token]
    if len(tokens) < 2:
        return False
    return tokens[-1] in local and (tokens[0] in local or local.startswith(tokens[0][0] + tokens[-1]))


def extract_contacts_from_html(
    html: str,
    source_url: str,
    page_title: str = "",
    selected_categories: list[str] | None = None,
) -> tuple[list[StaffContact], list[GeneralContact]]:
    staff: list[StaffContact] = []
    general: list[GeneralContact] = []
    soup = BeautifulSoup(html or "", "lxml")
    page_text = soup.get_text(" | ", strip=True)
    roster_role = _page_roster_role(soup, source_url, page_title, selected_categories)
    for email, method, raw, node in extract_email_evidence(html):
        fallback = raw if method == "public structured data" else safe_snippet(page_text, email)
        context = _context_for_node(node, email, fallback, roster_role.role if roster_role else "")
        panel_label = _panel_label_for_node(soup, node) if roster_role else ""
        if panel_label and panel_label.casefold() not in context[:200].casefold():
            context = f"{panel_label} | {context}"
        local_role = detect_role(context, selected_categories)
        used_roster_role = roster_role is not None and (
            local_role is None or local_role.category == SCHOOL_COUNSELING_CATEGORY
        )
        role_match = roster_role if used_roster_role else local_role
        if not role_match:
            continue
        exact_role = _extract_exact_role(context, email, role_match.role)
        if used_roster_role:
            exact_role = roster_role.role
        name = _extract_name(context, email, exact_role)
        if used_roster_role and (
            not name or (not panel_label and not _name_matches_email(name, email))
        ):
            continue
        credentials = extract_credentials(context)
        local = email.split("@", 1)[0].replace("-", "").replace("_", "").casefold()
        general_address = local in {part.replace(".", "") for part in GENERAL_LOCAL_PARTS}
        checked = utc_now()
        if role_match.role.casefold() in {"counseling office", "guidance office"} and not general_address:
            continue
        if general_address:
            general.append(GeneralContact(
                department_name=role_match.role if "Director" not in role_match.role else role_match.category,
                role_category=role_match.category,
                email=email,
                source_url=source_url,
                page_title=page_title,
                evidence_snippet=safe_snippet(context, email),
                extraction_method=method,
                date_checked=checked,
            ))
            continue
        if not name and is_general_department_context(context):
            # A personal address shown only near generic department language is
            # commonly a secretary/registrar, not evidence of a target role.
            continue
        confidence = (
            "Needs Review" if role_match.review_required
            else "Verified" if name
            else "Strong"
        )
        staff.append(StaffContact(
            staff_name=name or "Name not clearly stated",
            role=exact_role,
            role_category=role_match.category,
            email=email,
            source_url=source_url,
            page_title=page_title,
            evidence_snippet=safe_snippet(context, email),
            extraction_method=method,
            date_checked=checked,
            email_confidence=confidence,
            credentials=credentials,
        ))
    return staff, general
