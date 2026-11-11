"""Reader-proxy parsing tests (no network)."""

from bpc_web.proxy import article_from_reader_payload, parse_reader_payload, prose_window

PAYLOAD = """Title: Jensen Huang must resign

URL Source: https://www.economist.com/leaders/2026/11/11/jensen-huang-must-resign

Published Time: 2026-11-11T17:43:11.528Z

Markdown Content:
[Leaders](https://www.economist.com/leaders) | Nvidious

# Jensen Huang must resign

## And his should not be the only head to roll

J ensen Huang is a brilliant man—and a fool. The boss of Nvidia had an extramarital affair, and his
lover ran a cloud-computing firm that bought billions of dollars of Nvidia hardware.

## Continue with a free trial

### Get full access to our independent journalism for free

[Free trial](https://www.economist.com/subscribe)

## [More from Leaders](https://www.economist.com/leaders)

### [Some other story](https://www.economist.com/leaders/other)

3 min read
"""


def test_parse_envelope():
    meta, body = parse_reader_payload(PAYLOAD)
    assert meta["title"] == "Jensen Huang must resign"
    assert meta["date"].startswith("2026-11-11")
    assert body.lstrip().startswith("[Leaders]")
    assert "Markdown Content:" not in body


def test_parse_without_envelope():
    meta, body = parse_reader_payload("# Just markdown")
    assert meta == {}
    assert body == "# Just markdown"


def test_prose_window_keeps_the_story_only():
    page = "\n\n".join(
        [
            "[Menu](https://x.test/menu)",
            "Sign in",
            "# Headline",
            "A real paragraph of article prose that is long enough to count as body copy here, "
            "with enough words in it to clear the prose threshold comfortably, ending with a "
            "full stop.",
            "Another real paragraph of article prose that is likewise long enough to count as "
            "body copy, with plenty of words so the window keeps it, also ending with "
            "punctuation.",
            "3 min read",
            "[Footer link](https://x.test/footer)",
        ]
    )
    window = prose_window(page)
    assert "A real paragraph" in window
    assert "Another real paragraph" in window
    assert "Footer link" not in window
    assert "Sign in" not in window


def test_article_from_payload_flags_preview():
    article = article_from_reader_payload(PAYLOAD, "https://www.economist.com/x", windowed=True)
    assert article is not None
    assert article["title"] == "Jensen Huang must resign"
    assert article["paywalled_preview"] is True
    assert "brilliant man" in article["markdown"]
    # the subscription CTA and the "more from" module must be gone
    assert "free trial" not in article["markdown"].lower()
    assert "some other story" not in article["markdown"].lower()
    assert article["word_count"] > 0


def test_article_from_payload_rejects_empty():
    assert (
        article_from_reader_payload(
            "Title: x\n\nMarkdown Content:\n\nhi", "https://x.test", windowed=False
        )
        is None
    )
