"""Open-access resolver parsing (no network)."""

from offprint.oa import _titles_match, doi_from_url, parse_openalex, parse_unpaywall

OPENALEX = {
    "results": [
        {
            "display_name": "Deep learning",
            "doi": "https://doi.org/10.1038/nature14539",
            "is_oa": True,
            "open_access": {"is_oa": True, "oa_url": "https://example.org/paper.pdf"},
            "best_oa_location": {
                "is_oa": True,
                "url_for_landing_page": "https://example.org/paper",
                "source": {"display_name": "Institutional Repository"},
            },
        }
    ]
}

UNPAYWALL = {
    "is_oa": True,
    "doi": "10.1038/nature14539",
    "title": "Deep learning",
    "best_oa_location": {
        "url_for_landing_page": "https://repo.example/paper",
        "host_type": "repository",
    },
}


def test_doi_from_url():
    assert doi_from_url("https://doi.org/10.1038/nature14539") == "10.1038/nature14539"
    assert doi_from_url("https://www.nature.com/articles/nature14539") == ""
    assert doi_from_url("https://example.com/p/10.1000/xyz123?x=1") == "10.1000/xyz123"


def test_parse_openalex_prefers_landing_page():
    found = parse_openalex(OPENALEX)
    assert found["url"] == "https://example.org/paper"
    assert found["source"] == "openalex"
    assert found["doi"] == "10.1038/nature14539"


def test_parse_openalex_skips_closed_works():
    closed = {"results": [{"display_name": "X", "is_oa": False, "open_access": {"is_oa": False}}]}
    assert parse_openalex(closed) is None
    assert parse_openalex({}) is None


def test_parse_unpaywall():
    found = parse_unpaywall(UNPAYWALL)
    assert found["url"] == "https://repo.example/paper"
    assert found["source"] == "unpaywall"
    assert parse_unpaywall({"is_oa": False}) is None


def test_title_matching_guards_against_wrong_paper():
    assert _titles_match("Deep learning", "Deep learning") is True
    assert _titles_match("Deep learning", "Deep learning for weather") is True
    assert _titles_match("Deep learning", "Quantum chromodynamics at scale") is False
