import pytest

from ci_triage.analysis import analyze_log, redact


def analyze(log, **overrides):
    return analyze_log(
        **{
            "repo": "acme/app",
            "workflow_id": 42,
            "branch": "main",
            "job": "tests",
            "step": "Run checks",
            "log": log,
            "secrets": [],
            **overrides,
        }
    )


@pytest.mark.parametrize(
    ("log", "category"),
    [
        ("ModuleNotFoundError: No module named 'httpx'", "dependency"),
        ("Error: Cannot find module 'typescript'", "dependency"),
        ("FAILED tests/test_app.py::test_login - AssertionError", "test"),
        ("AssertionError: expected 5, got 4", "test"),
        ("src/app.py:12:1: F401 `os` imported but unused", "lint"),
        ("src/app.ts(7,2): error TS2322: Invalid type", "build"),
        ("Process completed with exit code 1.", "unknown"),
        ("", "unknown"),
    ],
)
def test_classification_has_concrete_evidence(log, category):
    result = analyze(log)
    assert result.category == category
    assert result.evidence
    assert result.suggestion


def test_grouping_ignores_timestamps_and_line_numbers_but_preserves_scope():
    first = analyze("2026-10-15T01:02:03.123Z src/app.py:12:1: F401 unused import")
    second = analyze("2026-10-16T06:02:03.987Z src/app.py:98:9: F401 unused import")
    assert first.fingerprint == second.fingerprint
    for scope in (
        {"branch": "release"},
        {"job": "other"},
        {"workflow_id": 43},
        {"repo": "other/app"},
    ):
        assert (
            analyze("src/app.py:12:1: F401 unused import", **scope).fingerprint != first.fingerprint
        )


def test_different_missing_modules_are_different_incidents():
    assert (
        analyze("ModuleNotFoundError: No module named 'httpx'").fingerprint
        != analyze("ModuleNotFoundError: No module named 'pytest'").fingerprint
    )


def test_redaction_before_evidence_is_returned():
    known = "private-configured-value"
    token = "ghp_" + "A" * 36
    log = f"\x1b[31mAssertionError: {known} {token} password=supersecret\x1b[0m"
    result = analyze(log, secrets=[known])
    assert all(secret not in result.evidence for secret in (known, token, "supersecret"))
    assert "[REDACTED]" in result.evidence
    assert "\x1b" not in result.evidence
    assert "password=[REDACTED]" in redact("password=secret")
