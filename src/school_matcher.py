from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

from rapidfuzz import fuzz

from .models import SchoolCandidate, SchoolInput, SchoolMatch

ABBREVIATIONS = {
    "hs": "high school",
    "h s": "high school",
    "ms": "middle school",
    "m s": "middle school",
    "es": "elementary school",
    "e s": "elementary school",
    "jr": "junior",
    "sr": "senior",
    "sch": "school",
}

GENERIC_SCHOOL_TOKENS = {
    "school", "high", "middle", "elementary", "academy", "institute", "center",
    "junior", "senior", "public", "regional", "charter", "the", "of", "and",
}


def split_school_name_and_hint(value: str) -> tuple[str, str]:
    """Treat a simple trailing comma value as a municipality hint, not part of the school name."""
    raw = re.sub(r"\s+", " ", value or "").strip()
    if "," not in raw:
        return raw, ""
    school, hint = (part.strip() for part in raw.split(",", 1))
    hint_tokens = set(normalize_school_name(hint).split())
    if hint_tokens and len(hint_tokens) <= 4 and not (hint_tokens & GENERIC_SCHOOL_TOKENS):
        return school, hint
    return raw, ""


def normalize_school_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("&", " and ")
    text = text.replace("’", "'").replace("`", "'")
    text = re.sub(r"[-‐‑‒–—]", " ", text)
    text = re.sub(r"['.]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    tokens = text.split()
    expanded: list[str] = []
    index = 0
    while index < len(tokens):
        pair = " ".join(tokens[index:index + 2])
        if pair in ABBREVIATIONS:
            expanded.extend(ABBREVIATIONS[pair].split())
            index += 2
        else:
            expanded.extend(ABBREVIATIONS.get(tokens[index], tokens[index]).split())
            index += 1
    return " ".join(expanded)


def school_aliases(value: str) -> set[str]:
    normalized = normalize_school_name(value)
    aliases = {normalized}
    replacements = {
        " high school": " hs",
        " middle school": " ms",
        " elementary school": " es",
        " junior high school": " jr high school",
        " senior high school": " sr high school",
    }
    for full, short in replacements.items():
        if full in normalized:
            aliases.add(normalize_school_name(normalized.replace(full, short)))
    if normalized.startswith("the "):
        aliases.add(normalized[4:])
    return aliases


class SchoolMatcher:
    """Conservative matcher that never auto-selects a weak or close match."""

    def __init__(self, candidates: list[SchoolCandidate]):
        self.candidates = candidates

    def match(self, raw_name: str) -> SchoolMatch:
        comparison_name, _location_hint = split_school_name_and_hint(raw_name)
        school_input = SchoolInput(raw_name=raw_name.strip(), normalized_name=normalize_school_name(comparison_name))
        if not school_input.normalized_name:
            return SchoolMatch(school_input, "Not Found", "Not Found", reason="The school name was blank.")

        exact = [c for c in self.candidates if normalize_school_name(c.canonical_name) == school_input.normalized_name]
        if len(exact) == 1:
            return SchoolMatch(school_input, "Verified", "Verified", 100.0, exact[0], reason="Exact NJDOE name match.")
        if len(exact) > 1:
            return SchoolMatch(
                school_input, "Needs Review", "Needs Review", 100.0,
                alternatives=exact, reason="Multiple NJDOE schools have this exact name.",
            )

        input_aliases = school_aliases(comparison_name)
        alias_matches = [c for c in self.candidates if input_aliases.intersection(school_aliases(c.canonical_name))]
        if len(alias_matches) == 1:
            return SchoolMatch(school_input, "Verified", "Verified", 99.0, alias_matches[0], reason="Exact normalized alias match.")
        if len(alias_matches) > 1:
            return SchoolMatch(
                school_input, "Needs Review", "Needs Review", 99.0,
                alternatives=alias_matches, reason="The normalized alias matches multiple NJDOE schools.",
            )

        scored = sorted(
            ((self._score(school_input.normalized_name, c), c) for c in self.candidates),
            key=lambda item: item[0], reverse=True,
        )
        if not scored or scored[0][0] < 76:
            return SchoolMatch(school_input, "Not Found", "Not Found", scored[0][0] if scored else 0.0, reason="No credible NJDOE match was found.")

        top_score, top = scored[0]
        close = [candidate for score, candidate in scored[:8] if score >= max(76, top_score - 4)]
        if len(close) > 1 or top_score < 89:
            return SchoolMatch(
                school_input, "Needs Review", "Needs Review", top_score,
                alternatives=close or [top], reason="More than one plausible school exists or the match is not strong enough to choose automatically.",
            )
        return SchoolMatch(
            school_input, "Likely", "Likely", top_score, top,
            alternatives=[candidate for _, candidate in scored[1:4]],
            reason="Strong fuzzy NJDOE name match; review the displayed district details.",
        )

    @staticmethod
    def _score(normalized_input: str, candidate: SchoolCandidate) -> float:
        name = normalize_school_name(candidate.canonical_name)
        ratio = fuzz.ratio(normalized_input, name)
        token = fuzz.token_sort_ratio(normalized_input, name)
        weighted = fuzz.WRatio(normalized_input, name)
        score = max(weighted, 0.55 * ratio + 0.45 * token)
        input_tokens = set(normalized_input.split())
        name_tokens = set(name.split())
        input_distinctive = input_tokens - GENERIC_SCHOOL_TOKENS
        name_distinctive = name_tokens - GENERIC_SCHOOL_TOKENS
        if input_distinctive and name_distinctive and not (input_distinctive & name_distinctive):
            return 0.0
        directional = {"north", "south", "east", "west"}
        if (input_tokens & directional) != (name_tokens & directional):
            score -= 12
        return round(max(0.0, min(100.0, score)), 2)


def confirm_school_match(match: SchoolMatch, candidate: SchoolCandidate) -> SchoolMatch:
    return replace(
        match,
        status="Verified",
        confidence="Verified",
        score=100.0,
        candidate=candidate,
        alternatives=[],
        reason="Confirmed by the user from NJDOE candidates.",
    )
