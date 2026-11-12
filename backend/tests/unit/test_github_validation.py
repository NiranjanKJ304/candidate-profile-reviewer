"""GitHub username / profile-URL validation, normalisation and SSRF resistance."""

from __future__ import annotations

import pytest

from app.services.github.validation import (
    InvalidGitHubInputError,
    parse_github_profile,
    profile_url,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("octocat", "octocat"),
        ("@octocat", "octocat"),
        ("  Octo-Cat  ", "Octo-Cat"),
        ("a", "a"),
        ("x" * 39, "x" * 39),
        ("user-123", "user-123"),
        ("https://github.com/octocat", "octocat"),
        ("https://github.com/octocat/", "octocat"),
        ("http://github.com/octocat", "octocat"),
        ("https://www.github.com/octocat", "octocat"),
        ("github.com/octocat", "octocat"),
        ("www.github.com/octocat", "octocat"),
        ("HTTPS://GITHUB.COM/OctoCat", "OctoCat"),
        ("https://github.com/octocat?tab=repositories", "octocat"),
        ("https://github.com/octocat#readme", "octocat"),
    ],
)
def test_valid_inputs(raw: str, expected: str) -> None:
    assert parse_github_profile(raw) == expected


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("x" * 40, "invalid_username"),
        ("-octocat", "invalid_username"),
        ("octocat-", "invalid_username"),
        ("octo--cat", "invalid_username"),
        ("octo_cat", "invalid_username"),
        ("octo cat", "malformed"),
        ("octo.cat", "malformed"),
        ("octo/cat", "malformed"),
        ("settings", "invalid_username"),
        ("https://github.com/settings", "invalid_username"),
        ("https://github.com/orgs", "invalid_username"),
        ("https://github.com/octocat/hello-world", "not_a_profile"),
        ("https://github.com/octocat/hello-world/issues", "not_a_profile"),
        ("https://github.com/", "not_a_profile"),
        ("https://gitlab.com/octocat", "unsupported_domain"),
        ("https://gist.github.com/octocat", "unsupported_domain"),
        ("https://api.github.com/users/octocat", "unsupported_domain"),
        ("https://github.com.evil.example/octocat", "unsupported_domain"),
        ("https://evilgithub.com/octocat", "unsupported_domain"),
        ("https://github.com:8443/octocat", "unsupported_domain"),
        ("https://user:pass@github.com/octocat", "malformed_url"),
        ("https://github.com@169.254.169.254/latest", "malformed_url"),
        ("http://169.254.169.254/latest/meta-data", "unsupported_domain"),
        ("http://localhost/octocat", "unsupported_domain"),
        ("ftp://github.com/octocat", "unsupported_scheme"),
        ("javascript://github.com/octocat", "unsupported_scheme"),
        ("file:///etc/passwd", "unsupported_scheme"),
        ("https://[::1]/octocat", "unsupported_domain"),
        ("https://github.com/octo%2Fcat", "invalid_username"),
        ("https://github.com/../../admin", "not_a_profile"),
        ("octocat\x00", "malformed"),
        ("a" * 301, "too_long"),
    ],
)
def test_rejected_inputs(raw: str, code: str) -> None:
    with pytest.raises(InvalidGitHubInputError) as info:
        parse_github_profile(raw)
    assert info.value.code == code, info.value.message


def test_fullwidth_homoglyphs_are_normalised_then_validated() -> None:
    # NFKC turns full-width characters into ASCII; the result must still be a valid name.
    fullwidth = "".join(chr(0xFF00 + ord(c) - 0x20) for c in "octocat")
    assert parse_github_profile(fullwidth) == "octocat"


def test_error_messages_never_echo_the_input() -> None:
    with pytest.raises(InvalidGitHubInputError) as info:
        parse_github_profile("https://evil.example/<script>")
    assert "<script>" not in info.value.message


def test_profile_url() -> None:
    assert profile_url("octocat") == "https://github.com/octocat"
