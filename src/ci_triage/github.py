import json
from pathlib import Path
from urllib.parse import urlparse

import httpx


class UpstreamError(Exception):
    """An upstream failure whose message is safe to persist in execution history."""


def _bounded_tail(response: httpx.Response, limit: int) -> tuple[str, bool]:
    tail = bytearray()
    total = 0
    for chunk in response.iter_bytes(chunk_size=65_536):
        total += len(chunk)
        tail.extend(chunk)
        if len(tail) > limit:
            del tail[:-limit]
    return tail.decode("utf-8", errors="replace"), total > limit


class GitHub:
    def __init__(
        self,
        settings,
        client: httpx.Client | None = None,
        download_client: httpx.Client | None = None,
    ):
        self.settings = settings
        self.client = client or httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {settings.github_token.get_secret_value()}",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": "n8n-ci-triage/0.1",
            },
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
        )
        # Storage redirects must never inherit the GitHub Authorization header.
        self.download_client = download_client or httpx.Client(
            timeout=settings.request_timeout_seconds, follow_redirects=False
        )

    def close(self):
        self.client.close()
        self.download_client.close()

    def request(self, method: str, path: str, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise UpstreamError("GitHub request failed; retry after checking connectivity") from exc
        if response.status_code >= 300:
            raise UpstreamError(f"GitHub returned HTTP {response.status_code}")
        return response.json()

    def pages(self, path: str, key: str | None = None, **params) -> list[dict]:
        items = []
        for page in range(1, 21):
            data = self.request("GET", path, params={**params, "per_page": 100, "page": page})
            batch = data[key] if key else data
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise UpstreamError("GitHub pagination safety limit exceeded; narrow the monitored scope")

    def get_run(self, run: dict) -> dict:
        return self.request(
            "GET", f"/repos/{run['repo']}/actions/runs/{run['run_id']}/attempts/{run['attempt']}"
        )

    def jobs(self, run: dict) -> list[dict]:
        return self.pages(
            f"/repos/{run['repo']}/actions/runs/{run['run_id']}/attempts/{run['attempt']}/jobs",
            "jobs",
        )

    def log(self, repo: str, job_id: int) -> tuple[str, bool, bool]:
        try:
            with self.client.stream("GET", f"/repos/{repo}/actions/jobs/{job_id}/logs") as response:
                if response.status_code in (404, 410):
                    return "The original job log is unavailable.", False, False
                if response.status_code == 200:
                    text, truncated = _bounded_tail(response, self.settings.max_log_bytes)
                    return text, truncated, True
                if response.status_code != 302:
                    raise UpstreamError(f"GitHub logs returned HTTP {response.status_code}")
                location = response.headers.get("location", "")
            parsed = urlparse(location)
            trusted_host = any(
                (parsed.hostname or "").endswith(suffix)
                for suffix in (".blob.core.windows.net", ".githubusercontent.com", ".amazonaws.com")
            )
            if parsed.scheme != "https" or not trusted_host or parsed.username or parsed.password:
                raise UpstreamError("GitHub returned an unexpected log download destination")
            with self.download_client.stream("GET", location) as response:
                if response.status_code in (404, 410):
                    return "The original job log is unavailable.", False, False
                if response.status_code != 200:
                    raise UpstreamError(f"Log storage returned HTTP {response.status_code}")
                text, truncated = _bounded_tail(response, self.settings.max_log_bytes)
                return text, truncated, True
        except httpx.HTTPError as exc:
            raise UpstreamError("GitHub log download failed") from exc

    def find_issue(self, repo: str, marker: str) -> dict | None:
        return next(
            (
                issue
                for issue in self.pages(f"/repos/{repo}/issues", state="all")
                if "pull_request" not in issue and marker in (issue.get("body") or "")
            ),
            None,
        )

    def create_issue(self, repo: str, title: str, body: str) -> dict:
        return self.request("POST", f"/repos/{repo}/issues", json={"title": title, "body": body})

    def get_issue(self, repo: str, number: int) -> dict:
        return self.request("GET", f"/repos/{repo}/issues/{number}")

    def comment_once(self, repo: str, number: int, body: str, marker: str):
        issue = self.get_issue(repo, number)
        if marker in (issue.get("body") or ""):
            return
        comments = self.pages(f"/repos/{repo}/issues/{number}/comments")
        if not any(marker in (comment.get("body") or "") for comment in comments):
            self.request("POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body})

    def set_issue_state(self, repo: str, number: int, state: str):
        self.request("PATCH", f"/repos/{repo}/issues/{number}", json={"state": state})


class DemoGitHub:
    """Local fixture reader. It cannot publish to GitHub."""

    def __init__(self):
        path = Path(__file__).with_name("fixtures") / "scenarios.json"
        self.cases = json.loads(path.read_text())

    def case(self, run_id: int) -> dict:
        for case in self.cases.values():
            if case["payload"]["workflow_run"]["id"] == run_id:
                return case
        raise UpstreamError("Unknown demo run ID; use scripts/send_demo.py")

    def get_run(self, run: dict) -> dict:
        return self.case(run["run_id"])["payload"]["workflow_run"]

    def jobs(self, run: dict) -> list[dict]:
        return self.case(run["run_id"])["jobs"]

    def log(self, repo: str, job_id: int) -> tuple[str, bool, bool]:
        for case in self.cases.values():
            for job in case["jobs"]:
                if job["id"] == job_id:
                    return job["log"], False, True
        raise UpstreamError("Unknown demo job ID")

    def close(self):
        pass
