import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workflow_has_authenticated_stages_and_only_acknowledges_after_notify():
    workflow = json.loads((ROOT / "workflows/ci-triage.json").read_text())
    nodes = {node["name"]: node for node in workflow["nodes"]}
    for node in nodes.values():
        if node["type"].endswith("httpRequest"):
            assert node["credentials"]["httpHeaderAuth"]["id"] == "ciTriageInternalKey"
            assert node["retryOnFail"]
            assert node["parameters"]["options"]["timeout"] < 600_000
    assert nodes["Queue wake-up"]["parameters"]["authentication"] == "headerAuth"
    edges = workflow["connections"]
    assert edges["Analyze evidence"]["main"][0][0]["node"] == "Explain findings (LLM)"
    assert edges["Explain findings (LLM)"]["main"][0][0]["node"] == "Publish or save draft"
    assert edges["Publish or save draft"]["main"][0][0]["node"] == "Telegram or save draft"
    assert edges["Telegram or save draft"]["main"][0][0]["node"] == "Acknowledge run"
    assert edges["Acknowledge run"]["main"][0][0]["node"] == "Claim run"
