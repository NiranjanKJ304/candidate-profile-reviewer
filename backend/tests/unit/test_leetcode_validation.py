"""LeetCode username / profile URL validation (no network, ever)."""

from __future__ import annotations

import pytest

from app.services.leetcode.validation import (
    InvalidLeetCodeInputError,
    parse_leetcode_profile,
    profile_url,
)

FULL_WIDTH = "".join(chr(ord(c) + 0xFEE0) for c in "janedoe")


@pytest.mark.parametrize(
    ("value", "username"),
    [
        ("jane_doe", "jane_doe"),
        ("  jane-doe  ", "jane-doe"),
        ("@jane_doe", "jane_doe"),
        ("jane.doe99", "jane.doe99"),
        ("a" * 40, "a" * 40),
        ("https://leetcode.com/u/jane_doe/", "jane_doe"),
        ("https://leetcode.com/u/jane_doe", "jane_doe"),
        ("http://www.leetcode.com/u/jane_doe/", "jane_doe"),
        ("leetcode.com/u/jane_doe", "jane_doe"),
        ("https://leetcode.com/jane_doe/", "jane_doe"),  # legacy profile URL
        ("https://leetcode.com/u/jane_doe/?tab=solved#x", "jane_doe"),
        ("HTTPS://LEETCODE.COM/U/Jane_Doe/", "Jane_Doe"),
        (FULL_WIDTH, "janedoe"),
    ],
)
def test_valid(value: str, username: str) -> None:
    assert parse_leetcode_profile(value) == username


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("a" * 301, "too_long"),
        ("jane doe", "malformed"),
        ("jane" + chr(0), "malformed"),
        ("jane/doe", "malformed"),
        ("jane;doe", "malformed"),
        ("jane@doe", "malformed"),
        ("a" * 41, "invalid_username"),
        ("___", "invalid_username"),
        ("bad!name", "invalid_username"),
        ("problems", "invalid_username"),
        ("graphql", "invalid_username"),
        ("https://leetcode.com/problems/two-sum/", "not_a_profile"),
        ("https://leetcode.com/contest/weekly-contest-400/", "not_a_profile"),
        ("https://leetcode.com/discuss/", "not_a_profile"),
        ("https://leetcode.com/graphql", "not_a_profile"),
        ("https://leetcode.com/api/problems/all/", "not_a_profile"),
        ("https://leetcode.com/submissions/detail/1/", "not_a_profile"),
        ("https://leetcode.com/u/", "not_a_profile"),
        ("https://leetcode.com/u/jane/extra", "not_a_profile"),
        ("https://leetcode.com/", "not_a_profile"),
        ("https://leetcode.com/u/bad!name/", "invalid_username"),
        ("https://leetcode.com/u/a%2Fb/", "malformed_url"),
        ("https://leetcode.cn/u/jane_doe/", "unsupported_domain"),
        ("https://assets.leetcode.com/u/jane_doe/", "unsupported_domain"),
        ("https://leetcode.com.evil.example/u/jane/", "unsupported_domain"),
        ("http://169.254.169.254/latest/meta-data", "unsupported_domain"),
        ("http://localhost/u/jane/", "unsupported_domain"),
        ("https://leetcode.com:8443/u/jane/", "unsupported_domain"),
        ("https://user:pw@leetcode.com/u/jane/", "malformed_url"),
        ("https://leetcode.com@evil.example/u/jane/", "malformed_url"),
        ("ftp://leetcode.com/u/jane/", "unsupported_scheme"),
        ("javascript://leetcode.com/u/jane/", "unsupported_scheme"),
        ("https://leetcode.com:abc/u/jane/", "malformed_url"),
    ],
)
def test_rejected(value: str, code: str) -> None:
    with pytest.raises(InvalidLeetCodeInputError) as info:
        parse_leetcode_profile(value)
    assert info.value.code == code


def test_error_message_does_not_echo_input() -> None:
    with pytest.raises(InvalidLeetCodeInputError) as info:
        parse_leetcode_profile("<script>alert(1)</script>")
    assert "<script>" not in info.value.message


def test_profile_url_is_canonical() -> None:
    assert profile_url("jane_doe") == "https://leetcode.com/u/jane_doe/"
