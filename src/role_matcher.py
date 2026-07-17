from __future__ import annotations

import re
from dataclasses import dataclass


CLINICAL_CATEGORY = "Licensed or Clinical Mental-Health Professionals"
PSYCHOLOGY_CATEGORY = "School Psychology"
SOCIAL_WORK_CATEGORY = "School Social Work"
STUDENT_ASSISTANCE_CATEGORY = "Student Assistance and Substance-Use Support"
SCHOOL_COUNSELING_CATEGORY = "School Counseling"
LEADERSHIP_CATEGORY = "Mental-Health and Student-Services Leadership"
REVIEW_CATEGORY = "Possible Relevant Staff Requiring Review"

# Insertion order is the required display and export order.
ROLE_CATEGORIES: dict[str, tuple[str, ...]] = {
    CLINICAL_CATEGORY: (
        "school-based therapist", "mental-health therapist", "adolescent therapist",
        "licensed clinical social worker", "licensed social worker",
        "licensed professional counselor", "licensed associate counselor",
        "clinical mental-health counselor", "mental-health counselor", "mental-health clinician",
        "behavioral-health clinician", "school-based clinician", "clinical counselor",
        "mental health therapist", "clinical mental health counselor", "mental health counselor",
        "mental health clinician", "behavioral health clinician", "school based clinician",
        "school based therapist", "therapist", "lcsw", "lsw", "lpc", "lac",
    ),
    PSYCHOLOGY_CATEGORY: (
        "director of psychological services", "supervising school psychologist",
        "lead school psychologist", "school psychologist", "psychologist",
    ),
    SOCIAL_WORK_CATEGORY: (
        "supervising social worker", "lead social worker", "school social worker", "social worker",
    ),
    STUDENT_ASSISTANCE_CATEGORY: (
        "student assistance coordinator", "student assistance counselor",
        "substance awareness coordinator", "substance-use counselor", "substance use counselor",
        "prevention counselor", "crisis counselor", "crisis intervention specialist", "sac",
    ),
    SCHOOL_COUNSELING_CATEGORY: (
        "director of school counseling", "counseling department chair",
        "academic and personal counselor", "high school counselor", "middle school counselor",
        "school counselor", "guidance counselor", "personal counselor",
        "counseling office", "guidance office",
    ),
    LEADERSHIP_CATEGORY: (
        "director of mental-health services", "director of mental health services",
        "behavioral-health director", "behavioral health director",
        "supervisor of child study teams", "director of student services",
        "director of pupil services", "director of special services",
        "director of counseling", "counseling supervisor", "wellness director",
    ),
    REVIEW_CATEGORY: (
        "student support specialist", "intervention specialist", "behavior specialist",
        "wellness coordinator", "child study team member", "pupil services staff",
        "case manager", "child study team", "learning consultant", "cst",
    ),
}

STRONG_EXCLUSIONS = (
    "legal counsel", "general counsel", "board counsel", "camp counselor", "camp counselling",
    "admissions counselor", "admission counselor", "financial-aid counselor",
    "financial aid counselor", "financial counselor", "sales counselor", "attorney",
    "law firm", "outside vendor", "advertisement", "physical therapist",
    "occupational therapist", "speech therapist", "speech-language therapist",
    "respiratory therapist", "massage therapist",
)

COLLEGE_CAREER_ONLY = (
    "college-only counselor", "career-only counselor", "college counselor",
    "career counselor", "college and career counselor", "college & career counselor",
)

GENERAL_MARKERS = (
    "department", "office", "student services", "pupil services", "counseling",
    "guidance", "child study team", "wellness center", "support services",
    "mental health", "behavioral health", "psychological services", "social work",
)

LEADERSHIP_EVIDENCE_TERMS = (
    "mental health", "behavioral health", "counsel", "psycholog", "social work",
    "student support", "student services", "pupil services", "child study", "wellness",
    "crisis", "substance", "therap",
)

CREDENTIAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Licensed Clinical Social Worker", r"\blicensed[\s-]+clinical[\s-]+social[\s-]+worker\b"),
    ("Licensed Social Worker", r"\blicensed[\s-]+social[\s-]+worker\b"),
    ("Licensed Professional Counselor", r"\blicensed[\s-]+professional[\s-]+counselor\b"),
    ("Licensed Associate Counselor", r"\blicensed[\s-]+associate[\s-]+counselor\b"),
    ("LCSW", r"\bl\.?\s*c\.?\s*s\.?\s*w\.?\b"),
    ("LSW", r"\bl\.?\s*s\.?\s*w\.?\b"),
    ("LPC", r"\bl\.?\s*p\.?\s*c\.?\b"),
    ("LAC", r"\bl\.?\s*a\.?\s*c\.?\b"),
    ("LMHC", r"\bl\.?\s*m\.?\s*h\.?\s*c\.?\b"),
    ("LMFT", r"\bl\.?\s*m\.?\s*f\.?\s*t\.?\b"),
    ("LCADC", r"\bl\.?\s*c\.?\s*a\.?\s*d\.?\s*c\.?\b"),
    ("CADC", r"\bc\.?\s*a\.?\s*d\.?\s*c\.?\b"),
    ("NCC", r"\bn\.?\s*c\.?\s*c\.?\b"),
    ("MSW", r"\bm\.?\s*s\.?\s*w\.?\b"),
    ("PsyD", r"\bpsy\.?\s*d\.?\b"),
    ("PhD", r"\bph\.?\s*d\.?\b"),
    ("EdD", r"\bed\.?\s*d\.?\b"),
)


@dataclass(frozen=True, slots=True)
class RoleMatch:
    role: str
    category: str
    score: int
    review_required: bool = False


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    compact = phrase.casefold().replace("-", " ")
    if compact in {"sac", "cst", "lcsw", "lsw", "lpc", "lac"}:
        return re.compile(rf"\b{re.escape(compact)}\b", re.I)
    tokens = [re.escape(token) for token in re.split(r"[\s-]+", phrase) if token]
    return re.compile(rf"\b{'[\\s-]+'.join(tokens)}s?\b", re.I)


def extract_credentials(text: str) -> str:
    found = [label for label, pattern in CREDENTIAL_PATTERNS if re.search(pattern, text or "", re.I)]
    return ", ".join(dict.fromkeys(found))


def strip_credentials(text: str) -> str:
    value = text or ""
    for _label, pattern in CREDENTIAL_PATTERNS:
        value = re.sub(pattern, " ", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip()


def detect_role(text: str, selected_categories: list[str] | None = None) -> RoleMatch | None:
    value = re.sub(r"\s+", " ", text or "").strip()
    lowered = value.casefold()
    if not value or any(term in lowered for term in STRONG_EXCLUSIONS):
        return None

    allowed = set(selected_categories or ROLE_CATEGORIES)
    matches: list[RoleMatch] = []
    category_count = len(ROLE_CATEGORIES)
    for category_index, (category, phrases) in enumerate(ROLE_CATEGORIES.items()):
        if category not in allowed:
            continue
        for phrase in phrases:
            found = _phrase_pattern(phrase).search(value)
            if not found:
                continue
            if phrase == "director of special services" and not any(
                term in lowered.replace(found.group(0).casefold(), "") for term in LEADERSHIP_EVIDENCE_TERMS
            ):
                continue
            specificity = len(phrase.split()) * 100 + len(phrase)
            category_priority = (category_count - category_index) * 10_000
            matches.append(RoleMatch(
                role=found.group(0).strip(),
                category=category,
                score=category_priority + specificity,
                review_required=category == REVIEW_CATEGORY,
            ))

    best = max(matches, key=lambda item: item.score, default=None)
    if best is None:
        return None
    if any(term in lowered for term in COLLEGE_CAREER_ONLY) and best.category == SCHOOL_COUNSELING_CATEGORY:
        # A separate, explicit school-counselor title still qualifies; a college/career-only title does not.
        explicit_school_title = any(
            _phrase_pattern(phrase).search(value)
            for phrase in ROLE_CATEGORIES[SCHOOL_COUNSELING_CATEGORY]
            if phrase not in {"counseling office", "guidance office"}
        )
        if not explicit_school_title:
            return None
    return best


def is_general_department_context(text: str) -> bool:
    lowered = (text or "").casefold()
    return any(marker in lowered for marker in GENERAL_MARKERS)


def role_sort_key(category: str) -> int:
    order = list(ROLE_CATEGORIES)
    return order.index(category) if category in order else len(order)
