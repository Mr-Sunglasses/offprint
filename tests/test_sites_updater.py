"""Site table updater: validation and digest logic (no network)."""

from offprint.sites_updater import _digest, _looks_valid


def test_digest_is_stable_and_short():
    assert _digest("abc") == _digest("abc")
    assert _digest("abc") != _digest("abd")
    assert len(_digest("abc")) == 16


def test_valid_table_is_accepted():
    body = "var defaultSites = {" + '  "Site": { domain: "example.com" },\n' * 600 + "}"
    assert _looks_valid(body) is True


def test_junk_is_rejected():
    assert _looks_valid("") is False
    assert _looks_valid("<html>404</html>") is False
    assert _looks_valid('var defaultSites = {"a": {domain: "a.com"}}') is False
