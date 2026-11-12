"""Export tests: HTML rendering, EPUB structure, image policy."""

import io
import zipfile

import pytest

from offprint.export import build_document_html, build_epub, markdown_to_html

ARTICLE = {
    "url": "https://example.com/story",
    "title": "Export Me",
    "author": "A. Writer",
    "date": "2026-03-04",
    "site": "Example Daily",
    "markdown": (
        "# Export Me\n\n"
        "A first paragraph that is long enough to be real prose.\n\n"
        "## A heading\n\n"
        "- one\n- two\n\n"
        "> a quote\n\n"
        "![photo](https://cdn.example.com/photo.jpg)\n"
    ),
    "images": ["https://cdn.example.com/photo.jpg"],
}


def test_markdown_to_html_structure():
    html = markdown_to_html(ARTICLE["markdown"])
    assert "<h2>" in html
    assert "<ul>" in html
    assert "<blockquote>" in html


def test_document_has_title_byline_and_source():
    html = build_document_html(ARTICLE, inline_images=False)
    assert "<h1>Export Me</h1>" in html
    assert "A. Writer" in html
    assert "https://example.com/story" in html
    assert "<img" not in html


def test_unavailable_images_are_dropped_not_loaded_remotely():
    # image_map without the photo => the tag must disappear, otherwise Chromium
    # downloads the publisher's original and can balloon the PDF.
    html = build_document_html(ARTICLE, inline_images=True, image_map={})
    assert "<img" not in html


def test_inline_image_replacement():
    html = build_document_html(
        ARTICLE,
        inline_images=True,
        image_map={"https://cdn.example.com/photo.jpg": "data:image/jpeg;base64,AAAA"},
    )
    assert 'src="data:image/jpeg;base64,AAAA"' in html


@pytest.mark.asyncio
async def test_epub_is_a_valid_container():
    data = await build_epub({**ARTICLE, "images": []})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert names[0] == "mimetype"
        assert zf.read("mimetype") == b"application/epub+zip"
        assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert "META-INF/container.xml" in names
        opf = zf.read("OEBPS/content.opf").decode()
        assert "<dc:title>Export Me</dc:title>" in opf
        assert 'media-type="application/xhtml+xml"' in opf
        chapter = zf.read("OEBPS/chapter.xhtml").decode()
        assert "first paragraph" in chapter
        assert chapter.strip().startswith("<?xml")


@pytest.mark.asyncio
async def test_epub_without_images_still_works():
    data = await build_epub({"title": "Bare", "markdown": "Just text, no images.", "images": []})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "OEBPS/chapter.xhtml" in zf.namelist()
