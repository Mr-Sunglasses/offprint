"""Tests for HTML -> Markdown extraction."""

from bpc_web.extract import extract_article, json_ld_body, score_candidate, strip_paywall_text

ARTICLE_HTML = """
<html><head>
  <title>Fallback Title | Site</title>
  <meta property="og:site_name" content="Example Daily">
  <meta property="article:published_time" content="2026-03-04T10:00:00Z">
  <script type="application/ld+json">
  {"@type":"NewsArticle","headline":"The Real Headline","author":{"name":"A. Writer"},
   "datePublished":"2026-03-04","publisher":{"name":"Example Daily"}}
  </script>
</head><body>
  <nav><a href="/">Home</a><a href="/login">Log in</a></nav>
  <article>
    <h1>The Real Headline</h1>
    <p>First paragraph of the story with enough words to look like real prose, and it keeps going for a while.</p>
    <h2>A subheading</h2>
    <p>Second paragraph, also reasonably long, mentioning <a href="/ref">a link</a> and wording.</p>
    <img src="/images/photo.jpg" alt="A photo">
    <blockquote>Quoted line from somebody in the story.</blockquote>
    <p>Subscribe to continue reading</p>
  </article>
  <footer>© 2026 Example</footer>
</body></html>
"""


def test_extracts_metadata_and_markdown():
    article = extract_article(ARTICLE_HTML, "https://example.com/news/story")
    assert article["title"] == "The Real Headline"
    assert article["author"] == "A. Writer"
    assert article["date"] == "2026-03-04"
    assert article["site"] == "Example Daily"
    assert "First paragraph" in article["markdown"]
    assert "## A subheading" in article["markdown"]
    assert article["word_count"] > 20
    assert article["reading_minutes"] >= 1


def test_relative_images_become_absolute():
    article = extract_article(ARTICLE_HTML, "https://example.com/news/story")
    if "photo.jpg" in article["markdown"]:
        assert "https://example.com/images/photo.jpg" in article["markdown"]


def test_paywall_prompt_is_stripped():
    article = extract_article(ARTICLE_HTML, "https://example.com/news/story")
    assert "subscribe to continue reading" not in article["markdown"].lower()


def test_strip_paywall_text_keeps_early_matches():
    text = (
        "Log in to continue reading our coverage. "
        + "Body copy. " * 60
        + "Subscribe to continue reading"
    )
    cleaned = strip_paywall_text(text)
    assert cleaned.startswith("Log in to continue reading our coverage.")
    assert not cleaned.endswith("Subscribe to continue reading")


def test_json_ld_body_is_used_when_it_is_longer():
    body = " ".join(["This is a full paragraph of the article body that should win."] * 12)
    html = f"""
    <html><body><article><p>Short teaser only.</p></article>
    <script type="application/ld+json">
    {{"@type":"NewsArticle","headline":"H","articleBody":"{body}"}}
    </script></body></html>
    """
    assert json_ld_body(html)
    article = extract_article(html, "https://example.com/a")
    assert article["word_count"] > 100


def test_duplicate_blocks_are_dropped():
    html = """
    <html><body><article>
      <p>Interviews have been edited and condensed. Click on the topics below to get started.</p>
      <p>Interviews have been edited and condensed. Tap on the topics below to get started.</p>
      <p>The actual reporting begins here and runs on for a good number of words so it survives.</p>
    </article></body></html>
    """
    article = extract_article(html, "https://example.com/a")
    assert article["markdown"].lower().count("edited and condensed") == 1


def test_score_prefers_longer_text():
    short = {"text": "x" * 10}
    long = {"text": "x" * 4000}
    assert score_candidate(long) > score_candidate(short)


def test_empty_input_returns_empty_article():
    article = extract_article("", "https://example.com/a")
    assert article["markdown"] == ""
    assert article["word_count"] == 0
