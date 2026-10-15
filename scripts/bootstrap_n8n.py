"""Import this project's workflow and its private header credential into a fresh n8n."""

import json
import subprocess

from common import ROOT, local_env

credential = [
    {
        "id": "ciTriageInternalKey",
        "name": "CI Triage Internal API",
        "type": "httpHeaderAuth",
        "data": {"name": "X-Triage-Key", "value": local_env()["INTERNAL_API_KEY"]},
    }
]


def import_json(kind: str, data: bytes):
    # Standard input avoids putting a secret in command arguments or in a disk file.
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "-u",
            "node",
            "n8n",
            "n8n",
            "import:" + kind,
            "--input=/dev/stdin",
        ],
        input=data,
        check=True,
        cwd=ROOT,
    )


import_json("credentials", json.dumps(credential).encode())
import_json("workflow", (ROOT / "workflows/ci-triage.json").read_bytes())
print("Imported the workflow and credential. Review and Publish CI Triage in the n8n editor.")
