"""Unit tests for conduit.feeds."""

import asyncio
import socket
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conduit.feeds import (
    AggregatedFeedItem,
    ArticleContent,
    FeedItem,
    _check_url_safe,
    fetch_all_items,
    fetch_article_content,
    fetch_items,
    validate_feed,
)

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parsed(
    entries: list[Any],
    *,
    bozo: bool = False,
    bozo_exception: Exception | None = None,
) -> MagicMock:
    """Return a mock feedparser result."""
    parsed = MagicMock()
    parsed.bozo = bozo
    parsed.bozo_exception = bozo_exception
    parsed.entries = entries
    return parsed


def _make_entry(**kwargs: Any) -> MagicMock:
    """Return a mock feedparser entry with the given attributes set."""
    entry = MagicMock()
    for key, val in kwargs.items():
        setattr(entry, key, val)
    return entry


# ---------------------------------------------------------------------------
# FeedItem normalization — RSS 2.0
# ---------------------------------------------------------------------------


def test_rss_normalization_uses_published_and_summary() -> None:
    """fetch_items normalizes RSS 2.0 entries using published and summary."""
    entry = _make_entry(
        title="Article Title",
        link="https://example.com/article",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        summary="Article summary text",
    )
    parsed = _make_parsed([entry])

    with patch("feedparser.parse", return_value=parsed):
        items = asyncio.run(fetch_items("https://example.com/feed.xml"))

    assert len(items) == 1
    assert items[0]["title"] == "Article Title"
    assert items[0]["link"] == "https://example.com/article"
    assert items[0]["published"] == "Mon, 01 Jan 2024 00:00:00 +0000"
    assert items[0]["summary"] == "Article summary text"


# ---------------------------------------------------------------------------
# FeedItem normalization — Atom (updated + content fallbacks)
# ---------------------------------------------------------------------------


def test_atom_normalization_falls_back_to_updated_and_content() -> None:
    """fetch_items falls back to updated for published and content[0].value for summary."""
    content_item = MagicMock()
    content_item.value = "Full content value"
    entry = _make_entry(
        title="Atom Article",
        link="https://atom.example.com/1",
        updated="2024-06-15T12:00:00Z",
    )
    # No published attribute (MagicMock auto-creates it, but we override to empty string
    # so _str_field skips it and falls through to updated)
    entry.published = ""
    # No summary — MagicMock will have it, but set to empty to trigger content fallback
    entry.summary = ""
    entry.content = [content_item]

    parsed = _make_parsed([entry])

    with patch("feedparser.parse", return_value=parsed):
        items = asyncio.run(fetch_items("https://atom.example.com/feed"))

    assert len(items) == 1
    assert items[0]["published"] == "2024-06-15T12:00:00Z"
    assert items[0]["summary"] == "Full content value"


# ---------------------------------------------------------------------------
# FeedItem normalization — all fields absent → empty strings
# ---------------------------------------------------------------------------


def test_normalization_falls_back_to_empty_strings() -> None:
    """fetch_items returns empty strings when no recognized fields are present."""
    entry = _make_entry(
        title="",
        link="",
        published="",
        updated="",
        summary="",
    )
    # No content list — MagicMock will be a non-list object, not triggering fallback
    entry.content = None

    parsed = _make_parsed([entry])

    with patch("feedparser.parse", return_value=parsed):
        items = asyncio.run(fetch_items("https://example.com/sparse.xml"))

    assert len(items) == 1
    assert items[0]["title"] == ""
    assert items[0]["link"] == ""
    assert items[0]["published"] == ""
    assert items[0]["summary"] == ""


# ---------------------------------------------------------------------------
# validate_feed — fatal bozo raises ValueError
# ---------------------------------------------------------------------------


def test_validate_feed_raises_on_malformed_feed() -> None:
    """validate_feed raises ValueError when bozo_exception is a fatal exception."""
    exc = Exception("Connection refused")
    parsed = _make_parsed([], bozo=True, bozo_exception=exc)

    with (
        patch("conduit.feeds._check_url_safe"),
        patch("feedparser.parse", return_value=parsed),
    ):
        with pytest.raises(ValueError, match="malformed"):
            asyncio.run(validate_feed("https://not-a-feed.example.com/"))


# ---------------------------------------------------------------------------
# validate_feed — zero entries is valid
# ---------------------------------------------------------------------------


def test_validate_feed_accepts_empty_feed() -> None:
    """validate_feed does not raise for a valid feed with zero entries."""
    parsed = _make_parsed([])

    with (
        patch("conduit.feeds._check_url_safe"),
        patch("feedparser.parse", return_value=parsed),
    ):
        asyncio.run(validate_feed("https://example.com/empty.xml"))  # must not raise


# ---------------------------------------------------------------------------
# validate_feed — CharacterEncodingOverride is not fatal
# ---------------------------------------------------------------------------


def test_validate_feed_returns_feed_title_when_present() -> None:
    """validate_feed returns the feed-level title string when the feed has one."""
    parsed = _make_parsed([])
    parsed.feed.title = "My Blog"

    with (
        patch("conduit.feeds._check_url_safe"),
        patch("feedparser.parse", return_value=parsed),
    ):
        result = asyncio.run(validate_feed("https://example.com/feed.xml"))

    assert result == "My Blog"


def test_validate_feed_returns_none_when_title_is_empty() -> None:
    """validate_feed returns None when the feed title is an empty or whitespace string."""
    parsed = _make_parsed([])
    parsed.feed.title = "   "

    with (
        patch("conduit.feeds._check_url_safe"),
        patch("feedparser.parse", return_value=parsed),
    ):
        result = asyncio.run(validate_feed("https://example.com/feed.xml"))

    assert result is None


def test_validate_feed_returns_none_when_feed_object_missing() -> None:
    """validate_feed returns None gracefully when parsed.feed is None."""
    parsed = _make_parsed([])
    parsed.feed = None

    with (
        patch("conduit.feeds._check_url_safe"),
        patch("feedparser.parse", return_value=parsed),
    ):
        result = asyncio.run(validate_feed("https://example.com/feed.xml"))

    assert result is None


def test_validate_feed_accepts_character_encoding_override() -> None:
    """validate_feed does not raise when bozo_exception is CharacterEncodingOverride."""
    from feedparser.exceptions import CharacterEncodingOverride

    enc_exc = CharacterEncodingOverride("encoding mismatch")
    entry = _make_entry(
        title="Valid Title",
        link="https://example.com/1",
        published="2024-01-01",
        summary="Valid summary",
    )
    parsed = _make_parsed([entry], bozo=True, bozo_exception=enc_exc)

    with (
        patch("conduit.feeds._check_url_safe"),
        patch("feedparser.parse", return_value=parsed),
    ):
        asyncio.run(validate_feed("https://example.com/enc-feed.xml"))  # must not raise


# ---------------------------------------------------------------------------
# fetch_items — malformed feed returns empty list without raising
# ---------------------------------------------------------------------------


def test_fetch_items_returns_empty_list_on_malformed_feed() -> None:
    """fetch_items returns [] and does not raise when the feed is malformed."""
    exc = Exception("Parse error")
    parsed = _make_parsed([], bozo=True, bozo_exception=exc)

    with patch("feedparser.parse", return_value=parsed):
        items = asyncio.run(fetch_items("https://broken.example.com/feed"))

    assert items == []


# ---------------------------------------------------------------------------
# fetch_items — limit is respected
# ---------------------------------------------------------------------------


def test_fetch_items_respects_limit() -> None:
    """fetch_items returns at most `limit` items."""
    entries = [
        _make_entry(title=f"T{i}", link=f"L{i}", published="2024-01-01", summary="S")
        for i in range(10)
    ]
    parsed = _make_parsed(entries)

    with patch("feedparser.parse", return_value=parsed):
        items = asyncio.run(fetch_items("https://example.com/feed.xml", limit=3))

    assert len(items) == 3
    assert items[0]["title"] == "T0"
    assert items[2]["title"] == "T2"


# ---------------------------------------------------------------------------
# fetch_all_items — concurrent fetch, feed_url attached
# ---------------------------------------------------------------------------


def test_fetch_all_items_attaches_feed_url() -> None:
    """fetch_all_items attaches feed_url to every item from each feed."""
    feed1_item = FeedItem(title="T1", link="L1", published="P1", summary="S1")
    feed2_item = FeedItem(title="T2", link="L2", published="P2", summary="S2")

    async def _side_effect(url: str, limit: int = 50) -> list[FeedItem]:
        if "feed1" in url:
            return [feed1_item]
        return [feed2_item]

    urls = ["https://feed1.example.com/rss", "https://feed2.example.com/rss"]

    with patch("conduit.feeds.fetch_items", new=AsyncMock(side_effect=_side_effect)):
        items = asyncio.run(fetch_all_items(urls, per_feed_limit=10))

    assert len(items) == 2

    by_feed: dict[str, list[AggregatedFeedItem]] = {}
    for item in items:
        by_feed.setdefault(item["feed_url"], []).append(item)

    assert by_feed["https://feed1.example.com/rss"][0]["title"] == "T1"
    assert by_feed["https://feed2.example.com/rss"][0]["title"] == "T2"


# ---------------------------------------------------------------------------
# fetch_article_content — success
# ---------------------------------------------------------------------------


def test_fetch_article_content_success() -> None:
    """fetch_article_content returns a populated ArticleContent on success."""
    html = "<html><body><article>Full article text here.</article></body></html>"
    mock_metadata = MagicMock()
    mock_metadata.title = "Article Title"
    mock_metadata.author = "Jane Doe"
    mock_metadata.date = "2024-06-01"

    with (
        patch("conduit.feeds._check_url_safe"),
        patch("trafilatura.fetch_url", return_value=html),
        patch("trafilatura.extract_metadata", return_value=mock_metadata),
        patch("trafilatura.extract", return_value="Full article text here."),
    ):
        result: ArticleContent = asyncio.run(
            fetch_article_content("https://example.com/article")
        )

    assert result["url"] == "https://example.com/article"
    assert result["title"] == "Article Title"
    assert result["author"] == "Jane Doe"
    assert result["published"] == "2024-06-01"
    assert result["content"] == "Full article text here."
    assert result["truncated"] is False
    assert result["error"] == ""


# ---------------------------------------------------------------------------
# fetch_article_content — fetch failure
# ---------------------------------------------------------------------------


def test_fetch_article_content_fetch_failure() -> None:
    """fetch_article_content returns error ArticleContent when fetch_url returns None."""
    with (
        patch("conduit.feeds._check_url_safe"),
        patch("trafilatura.fetch_url", return_value=None),
    ):
        result = asyncio.run(fetch_article_content("https://example.com/article"))

    assert result["error"] != ""
    assert result["content"] == ""
    assert result["url"] == "https://example.com/article"


# ---------------------------------------------------------------------------
# fetch_article_content — extraction failure
# ---------------------------------------------------------------------------


def test_fetch_article_content_extraction_failure() -> None:
    """fetch_article_content returns error when extract returns None."""
    html = "<html><body>No article here.</body></html>"
    mock_metadata = MagicMock()
    mock_metadata.title = "Some Title"
    mock_metadata.author = ""
    mock_metadata.date = ""

    with (
        patch("conduit.feeds._check_url_safe"),
        patch("trafilatura.fetch_url", return_value=html),
        patch("trafilatura.extract_metadata", return_value=mock_metadata),
        patch("trafilatura.extract", return_value=None),
    ):
        result = asyncio.run(fetch_article_content("https://example.com/article"))

    assert result["error"] != ""
    assert result["content"] == ""
    assert result["title"] == "Some Title"


# ---------------------------------------------------------------------------
# fetch_article_content — truncation
# ---------------------------------------------------------------------------


def test_fetch_article_content_truncates_long_content() -> None:
    """fetch_article_content truncates content at 100 000 chars and sets truncated=True."""
    html = "<html><body><article>x</article></body></html>"
    long_content = "x" * 150_000

    with (
        patch("conduit.feeds._check_url_safe"),
        patch("trafilatura.fetch_url", return_value=html),
        patch("trafilatura.extract_metadata", return_value=None),
        patch("trafilatura.extract", return_value=long_content),
    ):
        result = asyncio.run(fetch_article_content("https://example.com/article"))

    assert len(result["content"]) == 100_000
    assert result["truncated"] is True
    assert result["error"] == ""


# ---------------------------------------------------------------------------
# _check_url_safe — helpers
# ---------------------------------------------------------------------------

# getaddrinfo return format: list of (family, type, proto, canonname, sockaddr)
# sockaddr for IPv4: (ip_str, port); for IPv6: (ip_str, port, flowinfo, scope_id)


def _addrinfo(ip: str) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    """Build a minimal getaddrinfo result for a single IP."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def _addrinfo6(ip: str) -> list[tuple[int, int, int, str, tuple[str, int, int, int]]]:
    """Build a minimal getaddrinfo result for a single IPv6 address."""
    return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0))]


# ---------------------------------------------------------------------------
# _check_url_safe — scheme validation
# ---------------------------------------------------------------------------


def test_check_url_safe_accepts_https() -> None:
    """https scheme with a public IP is accepted without raising."""
    with patch("socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
        _check_url_safe("https://example.com/feed.xml")  # must not raise


def test_check_url_safe_accepts_http() -> None:
    """http scheme with a public IP is accepted without raising."""
    with patch("socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
        _check_url_safe("http://example.com/feed.xml")  # must not raise


def test_check_url_safe_rejects_file_scheme() -> None:
    """file:// scheme is rejected before any DNS lookup."""
    with pytest.raises(ValueError, match="scheme"):
        _check_url_safe("file:///etc/passwd")


def test_check_url_safe_rejects_ftp_scheme() -> None:
    """ftp:// scheme is rejected before any DNS lookup."""
    with pytest.raises(ValueError, match="scheme"):
        _check_url_safe("ftp://example.com/feed.xml")


def test_check_url_safe_rejects_gopher_scheme() -> None:
    """gopher:// scheme is rejected before any DNS lookup."""
    with pytest.raises(ValueError, match="scheme"):
        _check_url_safe("gopher://example.com/")


def test_check_url_safe_rejects_missing_hostname() -> None:
    """URL with no hostname component is rejected."""
    with pytest.raises(ValueError, match="hostname"):
        _check_url_safe("https:///no-host/path")


# ---------------------------------------------------------------------------
# _check_url_safe — DNS failure
# ---------------------------------------------------------------------------


def test_check_url_safe_rejects_unresolvable_hostname() -> None:
    """Hostname that cannot be resolved raises ValueError."""
    with patch(
        "socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")
    ):
        with pytest.raises(ValueError, match="Could not resolve"):
            _check_url_safe("https://does-not-exist.invalid/feed")


# ---------------------------------------------------------------------------
# _check_url_safe — loopback
# ---------------------------------------------------------------------------


def test_check_url_safe_rejects_ipv4_loopback() -> None:
    """127.0.0.1 (loopback) is rejected."""
    with patch("socket.getaddrinfo", return_value=_addrinfo("127.0.0.1")):
        with pytest.raises(ValueError, match="non-public"):
            _check_url_safe("https://localhost/feed")


def test_check_url_safe_rejects_ipv6_loopback() -> None:
    """::1 (IPv6 loopback) is rejected."""
    with patch("socket.getaddrinfo", return_value=_addrinfo6("::1")):
        with pytest.raises(ValueError, match="non-public"):
            _check_url_safe("https://localhost/feed")


# ---------------------------------------------------------------------------
# _check_url_safe — AWS instance metadata endpoint
# ---------------------------------------------------------------------------


def test_check_url_safe_rejects_aws_metadata_endpoint() -> None:
    """169.254.169.254 (AWS instance metadata link-local) is rejected."""
    with patch("socket.getaddrinfo", return_value=_addrinfo("169.254.169.254")):
        with pytest.raises(ValueError, match="non-public"):
            _check_url_safe("http://169.254.169.254/latest/meta-data/")


def test_check_url_safe_rejects_link_local_via_hostname() -> None:
    """A hostname that resolves to a link-local address is rejected."""
    with patch("socket.getaddrinfo", return_value=_addrinfo("169.254.0.1")):
        with pytest.raises(ValueError, match="non-public"):
            _check_url_safe("https://metadata.internal/feed")


# ---------------------------------------------------------------------------
# _check_url_safe — RFC 1918 private ranges
# ---------------------------------------------------------------------------


def test_check_url_safe_rejects_rfc1918_10_block() -> None:
    """10.0.0.0/8 addresses are rejected."""
    with patch("socket.getaddrinfo", return_value=_addrinfo("10.0.0.1")):
        with pytest.raises(ValueError, match="non-public"):
            _check_url_safe("https://internal.corp/feed")


def test_check_url_safe_rejects_rfc1918_172_block() -> None:
    """172.16.0.0/12 addresses are rejected."""
    with patch("socket.getaddrinfo", return_value=_addrinfo("172.16.0.1")):
        with pytest.raises(ValueError, match="non-public"):
            _check_url_safe("https://internal.corp/feed")


def test_check_url_safe_rejects_rfc1918_192_168_block() -> None:
    """192.168.0.0/16 addresses are rejected."""
    with patch("socket.getaddrinfo", return_value=_addrinfo("192.168.1.100")):
        with pytest.raises(ValueError, match="non-public"):
            _check_url_safe("https://router.local/feed")


# ---------------------------------------------------------------------------
# _check_url_safe — hostname resolving to private IP (SSRF via DNS)
# ---------------------------------------------------------------------------


def test_check_url_safe_rejects_hostname_resolving_to_private_ip() -> None:
    """A public-looking hostname that resolves to a private IP is rejected."""
    with patch("socket.getaddrinfo", return_value=_addrinfo("192.168.0.1")):
        with pytest.raises(ValueError, match="non-public"):
            _check_url_safe("https://totally-legit-feed.com/rss")


def test_check_url_safe_rejects_if_any_resolved_address_is_private() -> None:
    """If a hostname resolves to both a public and a private IP, it is rejected."""
    mixed = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0)),
    ]
    with patch("socket.getaddrinfo", return_value=mixed):
        with pytest.raises(ValueError, match="non-public"):
            _check_url_safe("https://dual-stack.example.com/feed")


# ---------------------------------------------------------------------------
# _check_url_safe — integration with validate_feed
# ---------------------------------------------------------------------------


def test_validate_feed_propagates_ssrf_check_failure() -> None:
    """validate_feed raises ValueError (not calling feedparser) when URL is unsafe."""
    with (
        patch("socket.getaddrinfo", return_value=_addrinfo("192.168.1.1")),
        patch("feedparser.parse") as mock_parse,
    ):
        with pytest.raises(ValueError, match="non-public"):
            asyncio.run(validate_feed("https://internal.example.com/feed"))

    mock_parse.assert_not_called()


def test_validate_feed_propagates_bad_scheme_failure() -> None:
    """validate_feed raises ValueError for a non-http/https scheme without fetching."""
    with patch("feedparser.parse") as mock_parse:
        with pytest.raises(ValueError, match="scheme"):
            asyncio.run(validate_feed("file:///etc/passwd"))

    mock_parse.assert_not_called()


# ---------------------------------------------------------------------------
# _check_url_safe — integration with fetch_article_content
# ---------------------------------------------------------------------------


def test_fetch_article_content_rejects_private_ip() -> None:
    """fetch_article_content returns an error (no raise) for a private-IP URL."""
    with (
        patch("socket.getaddrinfo", return_value=_addrinfo("10.0.0.1")),
        patch("trafilatura.fetch_url") as mock_fetch,
    ):
        result = asyncio.run(fetch_article_content("https://internal.example.com/article"))

    assert result["error"] != ""
    assert result["content"] == ""
    mock_fetch.assert_not_called()


def test_fetch_article_content_rejects_aws_metadata() -> None:
    """fetch_article_content blocks requests to the AWS instance metadata endpoint."""
    with (
        patch("socket.getaddrinfo", return_value=_addrinfo("169.254.169.254")),
        patch("trafilatura.fetch_url") as mock_fetch,
    ):
        result = asyncio.run(
            fetch_article_content("http://169.254.169.254/latest/meta-data/iam/security-credentials/")
        )

    assert result["error"] != ""
    assert result["content"] == ""
    mock_fetch.assert_not_called()
