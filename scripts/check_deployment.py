"""Check a published demo workflow through its gateway without printing secrets."""

import argparse
import hashlib
import hmac
import http.client
import json
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

from common import ROOT, local_env


def request_json(url, headers=None, body=None, *, retry_for=0):
    request = urllib.request.Request(url, data=body, headers=headers or {})
    # Host-level proxies must not receive the internal authentication header.
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"}
        else urllib.request.build_opener()
    )
    deadline = time.monotonic() + retry_for
    while True:
        try:
            with opener.open(request, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504} or time.monotonic() >= deadline:
                raise
        except (OSError, http.client.HTTPException, ValueError):
            if time.monotonic() >= deadline:
                raise
        # Retrying a webhook reuses its delivery ID, body, and signature for deduplication.
        time.sleep(min(2, max(0, deadline - time.monotonic())))


def wait_ready(url, timeout):
    deadline = time.monotonic() + timeout
    last_error = "no healthy response"
    while True:
        try:
            status, body = request_json(url)
            if status == 200 and isinstance(body, dict) and body.get("status") == "ok":
                return
            last_error = f"HTTP {status}, service not ready"
        except (OSError, http.client.HTTPException, ValueError) as exc:
            last_error = (
                f"HTTP {exc.code}"
                if isinstance(exc, urllib.error.HTTPError)
                else type(exc).__name__
            )
        if time.monotonic() >= deadline:
            raise SystemExit(f"Readiness timed out: {url} ({last_error})")
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--n8n-port", type=int, default=5678)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if args.timeout <= 0 or not all(0 < port < 65536 for port in (args.api_port, args.n8n_port)):
        parser.error("Use a positive timeout and valid port numbers")
    env = local_env()
    if env.get("TRIAGE_MODE") != "demo" or env.get("PUBLISH_MODE") != "draft":
        parser.error("This check requires TRIAGE_MODE=demo and PUBLISH_MODE=draft")
    if env.get("TELEGRAM_BOT_TOKEN"):
        parser.error("Disable Telegram delivery before running this draft-only demo check")
    if env.get("LLM_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
        parser.error("Disable LLM calls before running this account-free demo check")
    if not env.get("WEBHOOK_SECRET") or not env.get("INTERNAL_API_KEY"):
        parser.error("Generate .env with scripts/configure.py first")

    api = f"http://127.0.0.1:{args.api_port}"
    auth = {"X-Triage-Key": env["INTERNAL_API_KEY"]}
    print("[1/5] Waiting for n8n readiness...", flush=True)
    wait_ready(f"http://127.0.0.1:{args.n8n_port}/healthz/readiness", args.timeout)
    print("[2/5] Checking the local triage API...", flush=True)
    wait_ready(api + "/healthz", args.timeout)
    print(f"[3/5] Checking gateway health: {args.gateway_url}", flush=True)
    wait_ready(args.gateway_url.rstrip("/") + "/healthz", args.timeout)
    payload = json.loads((ROOT / "src/ci_triage/fixtures/scenarios.json").read_text())[
        "dependency"
    ]["payload"]
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(env["WEBHOOK_SECRET"].encode(), body, hashlib.sha256).hexdigest()
    print("[4/5] Sending the signed webhook...", flush=True)
    status, accepted = request_json(
        args.gateway_url.rstrip("/") + "/webhooks/github",
        {
            "Content-Type": "application/json",
            "X-GitHub-Event": "workflow_run",
            "X-GitHub-Delivery": str(uuid.uuid4()),
            "X-Hub-Signature-256": "sha256=" + signature,
        },
        body,
        retry_for=args.timeout,
    )
    if status != 202 or accepted.get("status") not in {"queued", "duplicate"}:
        raise SystemExit("Expected an accepted demo event; check the gateway response")
    delivery = accepted["status"]
    print(f"[5/5] Webhook accepted ({delivery}). Waiting for the saved report...", flush=True)
    deadline = time.monotonic() + args.timeout
    while True:
        _, history = request_json(
            api + "/internal/runs", auth, retry_for=max(0, deadline - time.monotonic())
        )
        run = next((row for row in history["runs"] if row["key"] == accepted["run_key"]), None)
        if run and run["status"] == "done":
            report = run["report"]
            findings = report.get("findings", [])
            if (
                not findings
                or findings[0]["category"] != "dependency"
                or "No module named 'httpx'" not in findings[0]["evidence"]
                or report.get("publication", {}).get("mode") != "draft"
                or report.get("telegram", {}).get("mode") != "draft"
            ):
                raise SystemExit("The completed report did not match the expected draft demo")
            if delivery == "queued":
                print("PASS: gateway -> n8n -> completed dependency report")
            else:
                print("SAVED REPORT: this demo was already received; no new run was created")
            print(json.dumps({"status": "done", "report": report}, indent=2))
            return
        if run and run["status"] == "dead":
            raise SystemExit("Demo attempt is dead-lettered; inspect the failed n8n execution")
        if time.monotonic() >= deadline:
            state = run["status"] if run else "missing"
            raise SystemExit(f"Demo timed out ({state}); inspect the CI Triage workflow in n8n")
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Check failed: HTTP {exc.code}; see the last check stage above") from None
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise SystemExit(
            f"Check failed: {type(exc).__name__}; see the last check stage above"
        ) from None
