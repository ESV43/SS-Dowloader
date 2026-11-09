from __future__ import annotations

import html as html_lib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from ._config import USER_AGENT

logger = logging.getLogger(__name__)

SLIDE_RE = re.compile(
    r"https?://image\.slidesharecdn\.com/(?P<deck>[^/]+)/(?P<folder>\d+)/"
    r"(?P<base>.+?)-(?P<num>\d+)-(?P<width>2048|1920|1536|1024|960|638|480|320)"
    r"\.(?:jpg|jpeg|png|webp)",
    re.IGNORECASE,
)
TITLE_SUFFIX_RE = re.compile(r"\s*[|]\s*(?:PPTX?|PDF|KEY|ODP|SlideShare)\s*$", re.IGNORECASE)
NEXT_DATA_RE = re.compile(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.S | re.I)
CHALLENGE_MARKERS = ("Client Challenge", "_fs-ch-", "challenge-platform")


class ScraperError(Exception):
    pass


class InvalidUrlError(ScraperError):
    pass


class DeckUnavailableError(ScraperError):
    pass


class BlockedError(ScraperError):
    pass


@dataclass
class Deck:
    id: str
    url: str
    title: str
    author: str
    thumbnail: str
    total: int
    image_urls: list[str]
    fallback_urls: list[list[str]] = field(default_factory=list)

    @property
    def slug(self) -> str:
        value = re.sub(r"[^A-Za-z0-9._-]+", "-", self.title).strip("-._")
        return (value[:80] or "presentation").rstrip("-._")

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "author": self.author,
            "thumbnail": self.thumbnail,
            "slides": self.total,
            "image_urls": self.image_urls,
        }


def normalize_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise InvalidUrlError("Paste a SlideShare link to get started.")
    if not re.match(r"^https?://", value, re.IGNORECASE):
        value = "https://" + value
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"slideshare.net", "m.slideshare.net"}:
        raise InvalidUrlError("That link is not from slideshare.net.")
    if not parsed.path or parsed.path == "/":
        raise InvalidUrlError("That link does not point to a presentation.")
    blocked = ("/category/", "/login", "/signup", "/privacy", "/terms", "/features", "/pricing", "/search")
    if any(parsed.path.lower().startswith(prefix) for prefix in blocked):
        raise InvalidUrlError("That link does not point to a presentation.")
    return f"https://www.slideshare.net{parsed.path}"


def _clean_title(value: str) -> str:
    title = html_lib.unescape(value or "").strip()
    title = TITLE_SUFFIX_RE.sub("", title).strip()
    return re.sub(r"\s+", " ", title) or "Untitled presentation"


def _force_https(value: str) -> str:
    return re.sub(r"^http://", "https://", (value or "").strip())


def _image_candidates(slides: dict[str, Any], number: int) -> list[str]:
    host = _force_https(str(slides.get("host") or "https://image.slidesharecdn.com"))
    location = quote(str(slides.get("imageLocation") or ""), safe="-._~")
    base = quote(str(slides.get("title") or ""), safe="-._~")
    sizes = [s for s in slides.get("imageSizes") or [] if isinstance(s, dict) and s.get("width")]
    sizes.sort(key=lambda item: int(item.get("width") or 0), reverse=True)
    urls: list[str] = []
    for size in sizes:
        width = int(size["width"])
        quality = int(size.get("quality") or 85)
        url = f"{host}/{location}/{quality}/{base}-{number}-{width}.jpg"
        if url not in urls:
            urls.append(url)
    return urls


def deck_from_json(data: dict[str, Any], source_url: str) -> Deck:
    page_props = data.get("pageProps") or {}
    slideshow = page_props.get("slideshow")
    if not isinstance(slideshow, dict):
        raise DeckUnavailableError("That link does not point to a presentation.")

    total = int(slideshow.get("totalSlides") or 0)
    slides = slideshow.get("slides") or {}
    if total < 1 or not isinstance(slides, dict):
        raise DeckUnavailableError("That presentation has no slides available.")

    primary: list[str] = []
    fallback: list[list[str]] = []
    for number in range(1, total + 1):
        candidates = _image_candidates(slides, number)
        if not candidates:
            raise DeckUnavailableError("Slide images are not available for this presentation.")
        primary.append(candidates[0])
        fallback.append(candidates[1:])

    user = slideshow.get("user") or {}
    title = _clean_title(str(slideshow.get("title") or page_props.get("title") or "Untitled presentation"))
    canonical = _force_https(str(slideshow.get("canonicalUrl") or source_url))
    thumbnail = _force_https(str(slideshow.get("thumbnail") or primary[0]))
    author = str(user.get("name") or user.get("login") or "").strip()

    return Deck(
        id=str(slideshow.get("id") or canonical),
        url=canonical,
        title=title,
        author=author,
        thumbnail=thumbnail,
        total=total,
        image_urls=primary,
        fallback_urls=fallback,
    )


def deck_from_html(text: str, source_url: str) -> Deck:
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in CHALLENGE_MARKERS) and "__NEXT_DATA__" not in text:
        raise BlockedError(
            "SlideShare served an anti-bot challenge to this server. Please try again in a moment."
        )

    match = NEXT_DATA_RE.search(text)
    if match:
        try:
            data = json.loads(match.group(1))
            props = (data.get("props") or {}).get("pageProps") or {}
            if props.get("slideshow"):
                return deck_from_json({"pageProps": props}, source_url)
        except Exception:
            pass

    if "page no longer exists" in lowered or "<title>page not found" in lowered or '"statusCode":404' in text:
        # Only treat as unavailable if we truly have no slideshow data
        raise DeckUnavailableError("This presentation is no longer available.")

    matches = list(SLIDE_RE.finditer(text))
    if not matches:
        raise DeckUnavailableError("Could not find any slides on that page.")

    groups: dict[str, dict[int, tuple[int, str]]] = {}
    for m in matches:
        deck_key = m.group("deck")
        number = int(m.group("num"))
        width = int(m.group("width"))
        best = groups.setdefault(deck_key, {})
        cur = best.get(number)
        if cur is None or width > cur[0]:
            best[number] = (width, m.group(0))
    _, best = max(groups.items(), key=lambda kv: (len(kv[1]), max(kv[1]) if kv[1] else 0))
    total = max(best)
    primary = []
    for n in range(1, total + 1):
        if n not in best:
            raise DeckUnavailableError("Some slides could not be located on that page.")
        primary.append(best[n][1])

    title_match = re.search(r'property="og:title"\s+content="([^"]+)"', text)
    if not title_match:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    title = _clean_title(title_match.group(1) if title_match else "Untitled presentation")
    return Deck(
        id=next(iter(groups)),
        url=source_url,
        title=title,
        author="",
        thumbnail=primary[0],
        total=total,
        image_urls=primary,
        fallback_urls=[[] for _ in primary],
    )


def _get_text(url: str) -> str:
    try:
        from curl_cffi import requests as crequests

        r = crequests.get(
            url,
            impersonate="chrome131",
            timeout=25,
            headers={"Accept-Language": "en-US,en;q=0.9", "Cache-Control": "no-cache"},
            allow_redirects=True,
        )
        r.raise_for_status()
        return r.text
    except Exception as exc:
        logger.warning("curl_cffi fetch failed: %s", exc)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }
    r = httpx.get(url, headers=headers, follow_redirects=True, timeout=25)
    r.raise_for_status()
    return r.text


async def fetch_deck(raw_url: str) -> Deck:
    import asyncio

    url = normalize_url(raw_url)
    last_exc: Exception | None = None
    for _ in range(2):
        try:
            text = await asyncio.to_thread(_get_text, url)
            return deck_from_html(text, url)
        except (BlockedError, DeckUnavailableError, InvalidUrlError):
            raise
        except Exception as exc:
            last_exc = exc
            logger.warning("Fetch failed: %s", exc)
    raise ScraperError(str(last_exc) or "SlideShare did not respond. Please try again.")
