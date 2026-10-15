import hashlib
import re

from .models import Finding

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z\s*", re.MULTILINE)
SUGGESTIONS = {
    "dependency": "Check the dependency lockfile and the dependency groups installed by CI.",
    "test": "Reproduce the failing test with the same inputs and environment as this CI run.",
    "lint": "Run the reported linter locally and inspect the named rule before changing code.",
    "build": "Reproduce the build with the same compiler, runtime, and build configuration.",
    "unknown": "Inspect the linked job log. The available evidence does not establish a cause.",
}
RULES = [
    (
        "dependency",
        re.compile(
            r"ModuleNotFoundError:.*|ImportError:.*|Cannot find module ['\"].*|"
            r"No matching distribution found.*|ERR_PNPM_[A-Z_]+.*",
            re.I,
        ),
    ),
    (
        "lint",
        re.compile(
            r"[^\n]+:\d+:\d+: [A-Z]\d{3}\b.*|[^\n]*\berror\s+[^\n]+\s+"
            r"(?:@typescript-eslint/|no-unused-vars|react-hooks/)[^\n]*",
            re.I,
        ),
    ),
    ("test", re.compile(r"FAILED\s+[^\n]+|AssertionError[^\n]*|E\s+assert\s+[^\n]+")),
    ("build", re.compile(r"[^\n]*error TS\d+[^\n]*|fatal error:[^\n]+|BUILD FAILED[^\n]*")),
]


def redact(value: str, secrets: list[str] | None = None) -> str:
    value = ANSI.sub("", value)
    for secret in sorted(secrets or [], key=len, reverse=True):
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})", "[REDACTED]", value
    )
    value = re.sub(r"\b\d{6,15}:[A-Za-z0-9_-]{30,}\b", "[REDACTED]", value)
    value = re.sub(r"\brc-[a-fA-F0-9]{48}\b", "[REDACTED]", value)
    value = re.sub(
        r"(?i)\b(authorization|password|passwd|secret|api[_-]?key|access[_-]?token)"
        r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+",
        r"\1=[REDACTED]",
        value,
    )
    value = re.sub(r"(https?://)[^/\s:@]+:[^@\s]+@", r"\1[REDACTED]@", value)
    return value


def normalize_evidence(value: str) -> str:
    value = TIMESTAMP.sub("", value)
    value = re.sub(r"/home/runner/work/[^/]+/[^/]+/", "", value)
    value = re.sub(r":\d+(?=[:\s])", ":#", value)
    value = re.sub(r"\bline \d+\b", "line #", value, flags=re.I)
    value = re.sub(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{40}\b", "<volatile>", value, flags=re.I)
    return " ".join(value.split())


def analyze_log(
    *,
    repo: str,
    workflow_id: int,
    branch: str,
    job: str,
    step: str,
    log: str,
    secrets: list[str],
    truncated: bool = False,
    available: bool = True,
) -> Finding:
    cleaned = TIMESTAMP.sub("", redact(log, secrets))
    category = "unknown"
    evidence = "No diagnostic error line was found in the available log."
    for candidate, pattern in RULES:
        match = pattern.search(cleaned)
        if match:
            category, evidence = candidate, match.group(0).strip()
            break
    else:
        candidates = [
            line.strip()
            for line in cleaned.splitlines()
            if re.search(r"error|failed|exception|timed out", line, re.I)
        ]
        if candidates:
            evidence = candidates[-1]
    evidence = evidence[:1_000]
    identity = "\0".join(
        (repo.lower(), str(workflow_id), branch, job, category, normalize_evidence(evidence))
    )
    return Finding(
        fingerprint=hashlib.sha256(identity.encode()).hexdigest()[:32],
        job=redact(job, secrets),
        step=redact(step, secrets),
        category=category,
        evidence=evidence,
        suggestion=SUGGESTIONS[category],
        log_truncated=truncated,
        log_available=available,
    )


def markdown_report(run: dict, finding: dict) -> str:
    def safe(value: str) -> str:
        return value.replace("```", "'''").replace("@", "@\u200b")

    return (
        f"## CI failure: {safe(finding['job'])}\n\n"
        f"- Repository: `{safe(run['repo'])}`\n"
        f"- Workflow: {safe(run['workflow_name'])}\n"
        f"- Branch: `{safe(run['branch'])}`\n"
        f"- Failed step: {safe(finding['step'])}\n"
        f"- Category: **{finding['category']}**\n\n"
        f"### Evidence\n\n```text\n{safe(finding['evidence'])}\n```\n\n"
        f"### Suggested investigation\n\n{finding['suggestion']}\n\n"
        f"[Workflow run]({run['run_url']}) · Attempt {run['attempt']}\n\n"
        f"Commit: `{run['head_sha']}`\n\n"
        "Classification is rule based; this is evidence for investigation, "
        "not a proven root cause.\n"
        + (
            "\nOnly the bounded tail of this job log was analyzed.\n"
            if finding.get("log_truncated")
            else ""
        )
        + (
            "\nThe job log was unavailable; inspect GitHub for the original log.\n"
            if not finding.get("log_available", True)
            else ""
        )
    )
