from __future__ import annotations

import io
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import httpx
import img2pdf
import pikepdf
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from ._config import (
    MEDIA_TYPES,
    PDF_LAYOUT,
    PDF_QUALITY,
    PDF_WIDTH,
    PPTX_QUALITY,
    PPTX_WIDTH,
    SLIDE_HEIGHT_IN,
    SLIDE_WIDTH_IN,
    USER_AGENT,
    WORK_DIR,
)
from ._scraper import Deck

try:
    Image.MAX_IMAGE_PIXELS = 200_000_000
except Exception:
    pass


class BuildError(Exception):
    pass


@dataclass
class SourceImage:
    index: int
    url: str
    ext: str
    data: bytes

    @property
    def file_name(self) -> str:
        return f"slide-{self.index:03d}{self.ext}"


@dataclass
class BuildResult:
    path: Path
    filename: str
    media_type: str
    size: int
    source_size: int


def _ext_from(url: str, content_type: str = "") -> str:
    ctype = (content_type or "").lower()
    if "webp" in ctype:
        return ".webp"
    if "png" in ctype:
        return ".png"
    if "gif" in ctype:
        return ".gif"
    m = re.search(r"\.(webp|png|jpe?g|gif)(?:$|\?)", url, re.I)
    if m:
        ext = m.group(1).lower()
        return ".jpg" if ext == "jpeg" else f".{ext}"
    return ".jpg"


def _headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": "https://www.slideshare.net/",
    }


def _download_one(client: httpx.Client, index: int, urls: list[str]) -> SourceImage:
    last = None
    for url in urls:
        try:
            r = client.get(url, headers=_headers(), follow_redirects=True, timeout=30)
            r.raise_for_status()
            data = r.content
            if len(data) < 600:
                raise BuildError(f"Slide {index} returned an empty image.")
            return SourceImage(index=index, url=url, ext=_ext_from(url, r.headers.get("content-type", "")), data=data)
        except Exception as exc:
            last = exc
    raise BuildError(f"Could not download slide {index}: {last}")


def download_sources(deck: Deck) -> list[SourceImage]:
    pairs = [[url] + fallbacks for url, fallbacks in zip(deck.image_urls, deck.fallback_urls or [[] for _ in deck.image_urls])]
    results: list[SourceImage] = []
    with httpx.Client(http2=False) as client:
        with ThreadPoolExecutor(max_workers=min(12, max(4, len(pairs)))) as pool:
            futures = [pool.submit(_download_one, client, i, urls) for i, urls in enumerate(pairs, 1)]
            for fut in as_completed(futures):
                results.append(fut.result())
    results.sort(key=lambda s: s.index)
    if len(results) != deck.total:
        raise BuildError("Some slides could not be downloaded.")
    return results


def _normalize_image(image: Image.Image) -> Image.Image:
    if getattr(image, "is_animated", False):
        try:
            image.seek(0)
        except Exception:
            pass
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    if image.mode != "RGB":
        return image.convert("RGB")
    return image.copy()


def optimize_image(source: SourceImage, width: int, quality: int) -> tuple[bytes, int, int]:
    with Image.open(io.BytesIO(source.data)) as original:
        image = _normalize_image(original)
        if image.width > width:
            height = max(1, round(image.height * width / image.width))
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        final_w, final_h = image.size
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=quality, optimize=True, progressive=False, subsampling=2)
    return out.getvalue(), final_w, final_h


def create_zip(sources: list[SourceImage], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(".tmp.zip")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for item in sorted(sources, key=lambda s: s.index):
            zf.writestr(item.file_name, item.data)
    tmp.replace(destination)


def create_pdf(images: list[tuple[bytes, int, int]], destination: Path, title: str) -> None:
    if not images:
        raise BuildError("No slides to write.")
    layout = img2pdf.get_layout_fun(pagesize=PDF_LAYOUT)
    raw = img2pdf.convert([data for data, _, _ in images], layout_fun=layout)
    with pikepdf.open(io.BytesIO(raw)) as pdf:
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta["dc:title"] = title
            meta["dc:creator"] = ["SlideSwift"]
            meta["pdf:Producer"] = "SlideSwift / pikepdf"
        pdf.save(destination, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate, preserve_pdfa=False)


def create_pptx(images: list[tuple[bytes, int, int]], destination: Path, title: str) -> None:
    prs = Presentation()
    prs.slide_width = Inches(SLIDE_WIDTH_IN)
    prs.slide_height = Inches(SLIDE_HEIGHT_IN)
    layout = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[0]
    for data, _, _ in images:
        slide = prs.slides.add_slide(layout)
        slide.shapes.add_picture(io.BytesIO(data), 0, 0, width=prs.slide_width, height=prs.slide_height)
    try:
        prs.core_properties.title = title
        prs.core_properties.subject = "Downloaded from SlideShare via SlideSwift"
    except Exception:
        pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(destination))


def _deck_dir(deck: Deck) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", deck.id)[:80] or "deck"
    return WORK_DIR / safe


def build_deck(deck: Deck, fmt: str) -> BuildResult:
    if fmt not in {"pdf", "pptx", "zip"}:
        raise BuildError("Unsupported format.")
    sources = download_sources(deck)
    source_size = sum(len(s.data) for s in sources)
    out_dir = _deck_dir(deck)
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"{deck.slug}.{fmt}"
    if fmt == "zip":
        create_zip(sources, output)
    else:
        width, quality = (PDF_WIDTH, PDF_QUALITY) if fmt == "pdf" else (PPTX_WIDTH, PPTX_QUALITY)
        optimized = [optimize_image(s, width, quality) for s in sources]
        if fmt == "pdf":
            create_pdf(optimized, output, deck.title)
        else:
            create_pptx(optimized, output, deck.title)
    if not output.exists() or output.stat().st_size < 1000:
        raise BuildError("The generated file is empty.")
    return BuildResult(
        path=output,
        filename=f"{deck.slug}.{fmt}" if fmt != "zip" else f"{deck.slug}-slides.zip",
        media_type=MEDIA_TYPES[fmt],
        size=output.stat().st_size,
        source_size=source_size,
    )
