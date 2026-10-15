"""Send one signed, deterministic GitHub-shaped webhook using only the standard library."""

import argparse
import hashlib
import hmac
import json
import urllib.error
import urllib.request
import uuid

from common import ROOT, local_env

cases = json.loads((ROOT / "src/ci_triage/fixtures/scenarios.json").read_text())
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("scenario", choices=cases)
parser.add_argument("--url", default="http://localhost:8000/webhooks/github")
parser.add_argument("--delivery", help="Reuse this value to test exact delivery deduplication")
args = parser.parse_args()
secret = local_env().get("WEBHOOK_SECRET", "")
if not secret:
    raise SystemExit("Run python3 scripts/configure.py first")
body = json.dumps(cases[args.scenario]["payload"], separators=(",", ":")).encode()
signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
request = urllib.request.Request(
    args.url,
    body,
    headers={
        "Content-Type": "application/json",
        "X-GitHub-Event": "workflow_run",
        "X-GitHub-Delivery": args.delivery or str(uuid.uuid4()),
        "X-Hub-Signature-256": "sha256=" + signature,
    },
)
try:
    with urllib.request.urlopen(request, timeout=15) as response:
        print(response.status, response.read().decode())
except urllib.error.HTTPError as exc:
    raise SystemExit(f"HTTP {exc.code}: {exc.read().decode()}") from exc
