"""Codeforces handle / profile URL validation (no network)."""

from __future__ import annotations

import pytest

from app.services.codeforces.validation import (
    InvalidCodeforcesInputError,
    parse_codeforces_profile,
    profile_url,
)

FULL_WIDTH_TOURIST = "".join(chr(ord(c) + 0xFEE0) for c in "tourist")


@pytest.mark.parametrize(
    ("value", "handle"),
    [
        ("tourist", "tourist"),
        ("  Um_nik  ", "Um_nik"),
        ("-is-this-fft-", "-is-this-fft-"),
        ("a.b_c-1", "a.b_c-1"),
        ("x", "x"),
        ("A" * 24, "A" * 24),
        ("https://codeforces.com/profile/tourist", "tourist"),
        ("http://codeforces.com/profile/tourist", "tourist"),
        ("https://www.codeforces.com/profile/tourist/", "tourist"),
        ("codeforces.com/profile/Petr", "Petr"),
        ("www.codeforces.com/profile/Petr", "Petr"),
        ("https://codeforces.com/profile/tourist?locale=en", "tourist"),
        ("https://codeforces.com/profile/tourist#top", "tourist"),
        ("HTTPS://CODEFORCES.COM/profile/tourist", "tourist"),
        ("https://codeforces.com/PROFILE/tourist", "tourist"),
        (FULL_WIDTH_TOURIST, "tourist"),  # full-width letters normalised (NFKC)
    ],
)
def test_valid(value: str, handle: str) -> None:
    assert parse_codeforces_profile(value) == handle


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("a" * 301, "too_long"),
        ("tour ist", "malformed"),
        ("tour\tist", "malformed"),
        ("tourist" + chr(0), "malformed"),
        ("tourist" + chr(127), "malformed"),
        ("tourist/x", "malformed"),
        ("user@host", "malformed"),
        ("c:\\path", "malformed"),
        ("a" * 25, "invalid_handle"),
        ("---", "invalid_handle"),
        ("...", "invalid_handle"),
        ("bad!name", "invalid_handle"),
        ("handle;other", "invalid_handle"),  # ';' would query several users in user.info
        ("näme", "invalid_handle"),
        ("https://codeforces.com/contest/1/problem/A", "not_a_profile"),
        ("https://codeforces.com/problemset/problem/1/A", "not_a_profile"),
        ("https://codeforces.com/blog/entry/1", "not_a_profile"),
        ("https://codeforces.com/submissions/tourist", "not_a_profile"),
        ("https://codeforces.com/profile/tourist/extra", "not_a_profile"),
        ("https://codeforces.com/profile/", "not_a_profile"),
        ("https://codeforces.com/", "not_a_profile"),
        ("https://codeforces.com/profile/bad!name", "invalid_handle"),
        ("https://codeforces.com/profile/a%2Fb", "malformed_url"),
        ("https://codeforces.com/profile/..%2F..", "malformed_url"),
        ("https://codeforces.ru/profile/tourist", "unsupported_domain"),
        ("https://m1.codeforces.com/profile/tourist", "unsupported_domain"),
        ("https://codeforces.com.evil.example/profile/tourist", "unsupported_domain"),
        ("https://evilcodeforces.com/profile/tourist", "unsupported_domain"),
        ("http://169.254.169.254/latest/meta-data", "unsupported_domain"),
        ("http://localhost/profile/tourist", "unsupported_domain"),
        ("http://[::1]/profile/tourist", "unsupported_domain"),
        ("https://codeforces.com:8443/profile/tourist", "unsupported_domain"),
        ("https://user:pw@codeforces.com/profile/tourist", "malformed_url"),
        ("https://codeforces.com@evil.example/profile/x", "malformed_url"),
        ("ftp://codeforces.com/profile/tourist", "unsupported_scheme"),
        ("javascript://codeforces.com/profile/tourist", "unsupported_scheme"),
        ("file:///etc/passwd", "unsupported_scheme"),
        ("https://codeforces.com:abc/profile/x", "malformed_url"),
    ],
)
def test_rejected(value: str, code: str) -> None:
    with pytest.raises(InvalidCodeforcesInputError) as info:
        parse_codeforces_profile(value)
    assert info.value.code == code


def test_error_message_does_not_echo_input() -> None:
    with pytest.raises(InvalidCodeforcesInputError) as info:
        parse_codeforces_profile("<script>alert(1)</script>")
    assert "<script>" not in info.value.message


def test_profile_url() -> None:
    assert profile_url("tourist") == "https://codeforces.com/profile/tourist"
