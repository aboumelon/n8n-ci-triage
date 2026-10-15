"""Debug the service pipeline without n8n. Normal deployments use the n8n workflow."""

import argparse
import json
import urllib.request

from common import local_env

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", default="http://localhost:8000")
args = parser.parse_args()
key = local_env()["INTERNAL_API_KEY"]


def post(path: str, body: dict):
    request = urllib.request.Request(
        args.url.rstrip("/") + path,
        json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Triage-Key": key},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


claim = post("/internal/claim", {})
if not claim["has_work"]:
    print("The queue is empty.")
else:
    context = {name: claim[name] for name in ("run_key", "lease_token")}
    for stage in ("analyze", "publish", "notify", "complete"):
        result = post("/internal/" + stage, context)
        if stage == "notify":
            print(json.dumps(result["report"], indent=2))
    print("Run acknowledged.")
