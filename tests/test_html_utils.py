from __future__ import annotations

from reader_sync.html_utils import (
    choose_url_from_html,
    extract_local_title,
    infer_from_filename,
    normalize_url,
    synthetic_url,
    tidy_extracted_url,
)


HTML_SAMPLE = """
<html>
  <head>
    <title>Example Title</title>
    <link rel=\"canonical\" href=\"https://example.com/articles?id=123&utm_source=rss\"/>
    <meta property=\"og:url\" content=\"https://example.com/articles?id=123\" />
  </head>
  <body>Body</body>
</html>
"""


def test_choose_url_prefers_canonical() -> None:
    url, source = choose_url_from_html(HTML_SAMPLE)
    assert url == "https://example.com/articles?id=123&utm_source=rss"
    assert source == "canonical"


def test_normalize_url_removes_tracking_params() -> None:
    url, _ = choose_url_from_html(HTML_SAMPLE)
    normalized = normalize_url(url, keep_params={"id"}, drop_prefixes={"utm_"})
    assert normalized == "https://example.com/articles?id=123"


def test_extract_local_title() -> None:
    assert extract_local_title(HTML_SAMPLE) == "Example Title"


def test_infer_from_filename_twitter() -> None:
    inferred = infer_from_filename("twitter.com_user_status_123456.html")
    assert inferred is not None
    url, source = inferred
    assert url == "https://x.com/user/status/123456"
    assert source == "inferred"


def test_synthetic_url() -> None:
    assert synthetic_url("deadbeef").startswith("https://local/doc/")


def test_tidy_extracted_url_handles_www() -> None:
    assert tidy_extracted_url("www.example.com/article") == "https://www.example.com/article"


def test_tidy_extracted_url_rejects_relative() -> None:
    assert tidy_extracted_url("/about") is None
