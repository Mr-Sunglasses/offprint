"""Discovery parsing tests (no network)."""

from bpc_web.discover import (
    _days_from_since,
    _looks_like_article,
    find_feed_link,
    parse_feed,
    parse_sitemap,
)

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Example</title>
  <item>
    <title>First story</title>
    <link>https://example.com/news/first-story</link>
    <pubDate>Wed, 11 Nov 2026 10:00:00 GMT</pubDate>
    <description>&lt;p&gt;A short &lt;b&gt;summary&lt;/b&gt; of the piece.&lt;/p&gt;</description>
  </item>
  <item>
    <title>Second story</title>
    <link>https://example.com/news/second-story</link>
    <pubDate>Tue, 10 Nov 2026 08:30:00 GMT</pubDate>
    <description>Another summary.</description>
  </item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom</title>
  <entry>
    <title>Atom story</title>
    <link href="https://example.com/a/atom-story"/>
    <published>2026-11-09T12:00:00Z</published>
    <summary>Atom summary text.</summary>
  </entry>
</feed>"""

SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/news/deep/story-one</loc><lastmod>2026-11-11</lastmod></url>
  <url><loc>https://example.com/news/deep/story-two</loc><lastmod>2026-11-10</lastmod></url>
  <url><loc>https://example.com/tags/politics</loc><lastmod>2026-11-11</lastmod></url>
</urlset>"""


def test_parse_rss():
    articles = parse_feed(RSS, "https://example.com/")
    assert len(articles) == 2
    assert articles[0]["title"] == "First story"
    assert articles[0]["url"] == "https://example.com/news/first-story"
    assert articles[0]["published"].startswith("2026-11-11")
    # HTML in descriptions must be flattened, not escaped
    assert "summary of the piece" in articles[0]["summary"]
    assert "<b>" not in articles[0]["summary"]


def test_parse_atom():
    articles = parse_feed(ATOM, "https://example.com/")
    assert len(articles) == 1
    assert articles[0]["url"] == "https://example.com/a/atom-story"
    assert articles[0]["published"].startswith("2026-11-09")


def test_parse_garbage_is_empty():
    assert parse_feed(b"not xml at all", "https://example.com/") == []


def test_find_feed_link():
    html = """<html><head>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml">
    </head></html>"""
    assert find_feed_link(html, "https://example.com/blog/") == "https://example.com/feed.xml"
    assert find_feed_link("<html></html>", "https://example.com/") == ""


def test_parse_sitemap():
    rows = parse_sitemap(SITEMAP)
    assert len(rows) == 3
    assert rows[0]["url"].endswith("/story-one")
    assert rows[0]["published"] == "2026-11-11"


def test_article_url_filter():
    assert _looks_like_article("https://example.com/news/2026/story-slug") is True
    assert _looks_like_article("https://example.com/tags/politics") is False
    assert _looks_like_article("https://example.com/video/2026/clip") is False
    assert _looks_like_article("https://example.com/photo.jpg") is False
    assert _looks_like_article("https://example.com/only-one-segment") is False


def test_since_parsing():
    assert _days_from_since("today") == 1
    assert _days_from_since("3d") == 3
    assert _days_from_since("7d") == 7
    assert _days_from_since("garbage") == 7
