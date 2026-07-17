from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from .models import (
    GeneralContact,
    SchoolCandidate,
    SchoolInput,
    SchoolMatch,
    SchoolSearchResult,
    StaffContact,
    WebsiteCandidate,
    WebsiteMatch,
)
from .school_matcher import normalize_school_name
from .utilities import utc_now


# Increment when extraction rules materially change so stale saved results are
# searched again instead of being presented as current-quality evidence.
CACHE_VERSION = 3
DEFAULT_RESULT_CACHE = Path("data/saved_school_results")
_WRITE_LOCK = threading.Lock()
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CachedSchoolResult:
    result: SchoolSearchResult
    saved_at: str

    @property
    def result_date(self) -> str:
        return self.result.checked_at or self.saved_at

    @property
    def date_label(self) -> str:
        try:
            value = datetime.fromisoformat(self.result_date)
            return f"{value.strftime('%B')} {value.day}, {value.year} at {value.strftime('%I:%M %p').lstrip('0')}"
        except (TypeError, ValueError):
            return self.result_date or "an earlier date"


def is_cacheable_result(result: SchoolSearchResult) -> bool:
    return result.status.startswith("Completed") or result.status in {
        "Website provided by user",
        "Partial result",
        "Website blocked crawling",
    }


def _cache_root(directory: str | Path | None = None) -> Path:
    return Path(directory or os.getenv("NJ_SCHOOL_FINDER_RESULT_CACHE_DIR", DEFAULT_RESULT_CACHE))


def _cache_path(name: str, directory: str | Path | None = None) -> Path | None:
    normalized = normalize_school_name(name)
    if not normalized:
        return None
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return _cache_root(directory) / f"{digest}.json"


def _dataclass_kwargs(model: type[T], value: dict[str, Any]) -> dict[str, Any]:
    allowed = {item.name for item in fields(model)}
    return {key: item for key, item in value.items() if key in allowed}


def _school_match(value: dict[str, Any]) -> SchoolMatch:
    data = dict(value)
    data["input_school"] = SchoolInput(**_dataclass_kwargs(SchoolInput, data.get("input_school", {})))
    candidate = data.get("candidate")
    data["candidate"] = (
        SchoolCandidate(**_dataclass_kwargs(SchoolCandidate, candidate))
        if isinstance(candidate, dict)
        else None
    )
    data["alternatives"] = [
        SchoolCandidate(**_dataclass_kwargs(SchoolCandidate, item))
        for item in data.get("alternatives", [])
        if isinstance(item, dict)
    ]
    return SchoolMatch(**_dataclass_kwargs(SchoolMatch, data))


def _website_match(value: dict[str, Any] | None) -> WebsiteMatch | None:
    if not isinstance(value, dict):
        return None
    data = dict(value)
    data["candidates"] = [
        WebsiteCandidate(**_dataclass_kwargs(WebsiteCandidate, item))
        for item in data.get("candidates", [])
        if isinstance(item, dict)
    ]
    return WebsiteMatch(**_dataclass_kwargs(WebsiteMatch, data))


def result_from_dict(value: dict[str, Any]) -> SchoolSearchResult:
    data = dict(value)
    data["school_match"] = _school_match(data.get("school_match", {}))
    data["website_match"] = _website_match(data.get("website_match"))
    data["contacts"] = [
        StaffContact(**_dataclass_kwargs(StaffContact, item))
        for item in data.get("contacts", [])
        if isinstance(item, dict)
    ]
    data["general_contacts"] = [
        GeneralContact(**_dataclass_kwargs(GeneralContact, item))
        for item in data.get("general_contacts", [])
        if isinstance(item, dict)
    ]
    data["review_contacts"] = [
        StaffContact(**_dataclass_kwargs(StaffContact, item))
        for item in data.get("review_contacts", [])
        if isinstance(item, dict)
    ]
    return SchoolSearchResult(**_dataclass_kwargs(SchoolSearchResult, data))


def save_result(result: SchoolSearchResult, directory: str | Path | None = None) -> bool:
    if not is_cacheable_result(result):
        return False
    saved_at = utc_now()
    payload = {
        "version": CACHE_VERSION,
        "saved_at": saved_at,
        "result": result.to_dict(),
    }
    aliases = {result.input_school_name, result.school_match.input_school.raw_name}
    if result.school:
        aliases.add(result.school.canonical_name)
    paths = {path for alias in aliases if (path := _cache_path(alias, directory)) is not None}
    root = _cache_root(directory)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        root.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK:
            for path in paths:
                temporary = path.with_suffix(".json.tmp")
                temporary.write_text(encoded, encoding="utf-8")
                temporary.replace(path)
        return bool(paths)
    except OSError:
        return False


def load_result(name: str, directory: str | Path | None = None) -> CachedSchoolResult | None:
    path = _cache_path(name, directory)
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != CACHE_VERSION or not isinstance(payload.get("result"), dict):
            return None
        result = result_from_dict(payload["result"])
        if not is_cacheable_result(result):
            return None
        return CachedSchoolResult(result=result, saved_at=str(payload.get("saved_at", "")))
    except (OSError, ValueError, TypeError, KeyError):
        return None
