"""Print saved reports. Authentication is read locally and is never printed."""

import argparse
import json
import urllib.request

from common import local_env

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", default="http://localhost:8000")
args = parser.parse_args()
request = urllib.request.Request(
    args.url.rstrip("/") + "/internal/runs",
    headers={
        "X-Triage-Key": local_env()["INTERNAL_API_KEY"],
    },
)
with urllib.request.urlopen(request, timeout=15) as response:
    print(json.dumps(json.load(response), indent=2))
