"""Export a stored article as EPUB or PDF.

EPUB is built by hand (it's just a zip with an OPF manifest), PDF is rendered by
the Chromium we already run, so neither needs a heavyweight dependency.
"""

from __future__ import annotations

import io
import re
import zipfile
from html import escape
from pathlib import Path
from urllib.parse import urljoin

import httpx
import markdown as md_lib

from .vendor.strategy import UA_NORMAL

IMAGE_LIMIT = 12
IMAGE_BYTES_LIMIT = 3 * 1024 * 1024
EPUB_MAX_WIDTH = 1400
PDF_MAX_WIDTH = 1100
TOTAL_INLINE_LIMIT = 12 * 1024 * 1024

PRINT_CSS = """
@page { size: A4; margin: 20mm 18mm; }
body { font-family: Georgia, 'Iowan Old Style', serif; font-size: 11.5pt; line-height: 1.6; color: #1b1814; }
h1 { font-size: 22pt; line-height: 1.2; margin: 0 0 6pt; }
.byline { font-family: -apple-system, Helvetica, sans-serif; font-size: 9pt; color: #6e6555; margin-bottom: 14pt; }
h2 { font-size: 15pt; margin: 18pt 0 6pt; }
h3 { font-size: 12.5pt; margin: 14pt 0 5pt; }
p { margin: 0 0 9pt; text-align: left; }
img { max-width: 100%; height: auto; display: block; margin: 10pt auto; }
blockquote { margin: 10pt 0; padding-left: 10pt; border-left: 2pt solid #9a3b12; font-style: italic; }
figcaption, .caption { font-family: -apple-system, Helvetica, sans-serif; font-size: 8.5pt; color: #6e6555; }
a { color: #9a3b12; text-decoration: none; }
hr { border: none; border-top: 0.5pt solid #d8cfbe; margin: 14pt 0; }
pre, code { font-family: Menlo, monospace; font-size: 9pt; }
pre { background: #f4eee1; padding: 8pt; border-radius: 4pt; white-space: pre-wrap; }
table { border-collapse: collapse; font-size: 9pt; }
th, td { border-bottom: 0.5pt solid #d8cfbe; padding: 4pt 6pt; text-align: left; }
.source { margin-top: 18pt; font-size: 8.5pt; color: #6e6555; }
"""

EPUB_CSS = """
body { font-family: serif; line-height: 1.55; }
h1 { font-size: 1.6em; line-height: 1.2; }
.byline { color: #666; font-size: 0.85em; margin-bottom: 1.2em; }
img { max-width: 100%; }
blockquote { border-left: 2px solid #9a3b12; padding-left: 0.8em; font-style: italic; }
.source { margin-top: 2em; font-size: 0.8em; color: #666; }
"""


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #


def markdown_to_html(markdown_text: str) -> str:
    return md_lib.markdown(
        markdown_text,
        extensions=["extra", "sane_lists", "smarty"],
        output_format="html5",
    )


def _byline(article: dict) -> str:
    parts = [p for p in (article.get("author"), article.get("date"), article.get("site")) if p]
    return " · ".join(str(p) for p in parts)


def build_document_html(
    article: dict, *, inline_images: bool, image_map: dict | None = None
) -> str:
    """Full HTML document for printing/EPUB, with optional image rewriting."""
    body = markdown_to_html(article.get("markdown", ""))
    if image_map is not None:
        for original, replacement in image_map.items():
            body = body.replace(f'src="{escape(original)}"', f'src="{replacement}"')
            body = body.replace(f"src='{escape(original)}'", f"src='{replacement}'")
        # Anything we could not download stays a remote URL — Chromium would then
        # embed the publisher's original (AVIF can decode to a ~40MP bitmap and
        # a single one ballooned a PDF to 35 MB). Drop those instead.
        body = re.sub(r'<img[^>]+src=["\']https?://[^"\']+["\'][^>]*>', "", body)
    elif not inline_images:
        body = re.sub(r"<img[^>]*>", "", body)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{escape(article.get("title") or "Article")}</title>
<style>{PRINT_CSS}</style>
</head><body>
<h1>{escape(article.get("title") or "")}</h1>
<p class="byline">{escape(_byline(article))}</p>
{body}
<p class="source">Source: {escape(article.get("url") or "")}<br>Retrieved by Offprint</p>
</body></html>"""


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #


async def _fetch_image(
    client: httpx.AsyncClient, url: str, referer: str
) -> tuple[bytes, str] | None:
    try:
        resp = await client.get(
            url,
            headers={
                "User-Agent": UA_NORMAL,
                "Referer": referer,
                "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
            },
        )
    except Exception:
        return None
    if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image/"):
        return None
    if len(resp.content) > IMAGE_BYTES_LIMIT:
        return None
    return resp.content, resp.headers["content-type"].split(";")[0]


def _downscale(content: bytes, mime: str, max_width: int) -> tuple[bytes, str]:
    """Shrink to a sane print/ebook width, re-encoding as JPEG when possible.

    Publishers serve 1400-4000px originals; embedding those verbatim produced a
    35 MB PDF for one article. 1100px @ q82 looks identical on paper.
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(content)) as img:
            img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, max(1, int(img.height * ratio))), Image.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=82, optimize=True, progressive=True)
            encoded = out.getvalue()
            if len(encoded) < len(content) or img.width <= max_width:
                return encoded, "image/jpeg"
    except Exception:
        pass
    return content, mime


def _ext_for(mime: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/avif": ".avif",
    }.get(mime, ".jpg")


async def collect_images(
    article: dict,
    client: httpx.AsyncClient | None = None,
    max_width: int = EPUB_MAX_WIDTH,
) -> dict[str, tuple[bytes, str]]:
    """Download the article's images once, keyed by URL (downscaled)."""
    urls = article.get("images") or re.findall(
        r"!\[[^\]]*\]\(([^)\s]+)\)", article.get("markdown", "")
    )
    own = client is None
    if own:
        client = httpx.AsyncClient(follow_redirects=True, timeout=25.0)
    found: dict[str, tuple[bytes, str]] = {}
    try:
        for url in list(dict.fromkeys(urls))[:IMAGE_LIMIT]:
            if not url.startswith("http"):
                url = urljoin(article.get("url", ""), url)
            result = await _fetch_image(client, url, article.get("url", ""))
            if result:
                found[url] = _downscale(result[0], result[1], max_width)
    finally:
        if own:
            await client.aclose()
    return found


# --------------------------------------------------------------------------- #
# EPUB
# --------------------------------------------------------------------------- #

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _slug(text: str, fallback: str = "article") -> str:
    ascii_text = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (ascii_text or fallback)[:60]


async def build_epub(article: dict) -> bytes:
    images = await collect_images(article)

    image_map: dict[str, str] = {}
    manifest: list[str] = []
    for index, (url, (_content, mime)) in enumerate(images.items()):
        filename = f"images/img{index:03d}{_ext_for(mime)}"
        image_map[url] = filename
        manifest.append(f'<item id="img{index}" href="{filename}" media-type="{mime}"/>')

    body = markdown_to_html(article.get("markdown", ""))
    for original, replacement in image_map.items():
        body = body.replace(f'src="{escape(original)}"', f'src="{replacement}"')

    chapter = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">
<head><title>{escape(article.get("title") or "Article")}</title>
<link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
<h1>{escape(article.get("title") or "")}</h1>
<p class="byline">{escape(_byline(article))}</p>
{body}
<p class="source">Source: {escape(article.get("url") or "")}<br/>Retrieved by Offprint</p>
</body></html>"""

    book_id = f"urn:bpc-fetch:{_slug(article.get('title') or '')}"
    modified = (article.get("date") or "2026-01-01")[:10] + "T00:00:00Z"
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{escape(book_id)}</dc:identifier>
    <dc:title>{escape(article.get("title") or "Article")}</dc:title>
    <dc:language>en</dc:language>
    <dc:creator>{escape(article.get("author") or article.get("site") or "")}</dc:creator>
    <dc:source>{escape(article.get("url") or "")}</dc:source>
    <dc:date>{escape((article.get("date") or "")[:10])}</dc:date>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
    {"".join(manifest)}
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>"""

    nav = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">
<head><title>Contents</title></head>
<body><nav epub:type="toc" id="toc"><h1>Contents</h1>
<ol><li><a href="chapter.xhtml">{escape(article.get("title") or "Article")}</a></li></ol>
</nav></body></html>"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        # mimetype must be first and uncompressed
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER_XML, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/chapter.xhtml", chapter, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/style.css", EPUB_CSS, compress_type=zipfile.ZIP_DEFLATED)
        for index, (_url, (content, mime)) in enumerate(images.items()):
            zf.writestr(
                f"OEBPS/images/img{index:03d}{_ext_for(mime)}",
                content,
                compress_type=zipfile.ZIP_DEFLATED,
            )
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #


async def build_pdf_html(article: dict) -> str:
    """HTML with images inlined as data URIs, ready for Chromium to print."""
    import base64

    images = await collect_images(article, max_width=PDF_MAX_WIDTH)
    image_map: dict[str, str] = {}
    budget = TOTAL_INLINE_LIMIT
    for url, (content, mime) in images.items():
        if len(content) > budget:
            continue
        budget -= len(content)
        image_map[url] = f"data:{mime};base64,{base64.b64encode(content).decode()}"
    return build_document_html(article, inline_images=True, image_map=image_map)


def epub_filename(article: dict) -> str:
    return f"{_slug(article.get('title') or '')}.epub"


def pdf_filename(article: dict) -> str:
    return f"{_slug(article.get('title') or '')}.pdf"


def write_preview(article: dict, out_dir: Path) -> Path:
    """Handy for debugging exports without a browser."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_slug(article.get('title') or '')}.html"
    path.write_text(build_document_html(article, inline_images=True), encoding="utf-8")
    return path
