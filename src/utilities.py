from __future__ import annotations

import hashlib
import html
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, TypeVar
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import tldextract

T = TypeVar("T")

THIRD_PARTY_DOMAINS = {
    "facebook.com", "greatschools.org", "instagram.com", "linkedin.com",
    "niche.com", "twitter.com", "x.com", "youtube.com", "wikipedia.org",
    "usnews.com", "mapquest.com", "yelp.com", "zillow.com", "realtor.com",
    "privateschoolreview.com", "publicschoolreview.com", "schooldigger.com",
    "zoominfo.com", "whitepages.com", "beenverified.com", "peoplefinder.com",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def dedupe_preserve_order(values: Iterable[T]) -> list[T]:
    seen: set[Any] = set()
    result: list[T] = []
    for value in values:
        key = value.casefold() if isinstance(value, str) else value
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def clean_school_lines(value: str) -> list[str]:
    return dedupe_preserve_order(line.strip() for line in value.splitlines() if line.strip())


def normalize_url(url: str, base: str = "") -> str:
    value = html.unescape((url or "").strip())
    if base:
        value = urljoin(base, value)
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urlparse(value)
    if not parsed.hostname:
        return ""
    host = parsed.hostname.lower().rstrip(".")
    port = f":{parsed.port}" if parsed.port and parsed.port not in {80, 443} else ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_")))
    return urlunparse((parsed.scheme.lower(), host + port, path, "", query, ""))


def registered_domain(url: str) -> str:
    host = (urlparse(normalize_url(url)).hostname or "").lower()
    ext = tldextract.extract(host)
    return ".".join(part for part in (ext.domain, ext.suffix) if part) or host


def is_third_party_domain(url: str) -> bool:
    domain = registered_domain(url)
    return any(domain == blocked or domain.endswith("." + blocked) for blocked in THIRD_PARTY_DOMAINS)


def same_domain(url_a: str, url_b: str) -> bool:
    return bool(registered_domain(url_a)) and registered_domain(url_a) == registered_domain(url_b)


def safe_snippet(text: str, needle: str = "", width: int = 280) -> str:
    cleaned = re.sub(r"\s+", " ", html.unescape(text or "")).strip()
    if len(cleaned) <= width:
        return cleaned
    index = cleaned.casefold().find(needle.casefold()) if needle else 0
    start = max(0, index - width // 2)
    end = min(len(cleaned), start + width)
    return ("…" if start else "") + cleaned[start:end].strip() + ("…" if end < len(cleaned) else "")


def cache_path(url: str, suffix: str = ".json") -> Path:
    root = Path(os.getenv("NJ_SCHOOL_FINDER_CACHE_DIR", "cache"))
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}{suffix}"


def write_json_cache(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def read_json_cache(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def decode_cloudflare_email(encoded: str) -> str:
    try:
        key = int(encoded[:2], 16)
        return "".join(chr(int(encoded[i:i + 2], 16) ^ key) for i in range(2, len(encoded), 2))
    except (ValueError, IndexError):
        return ""

