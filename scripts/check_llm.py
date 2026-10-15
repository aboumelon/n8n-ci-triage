"""Probe the configured free model's JSON output without exposing credentials."""

import json

from common import local_env
from configure_llm import SetupError, is_amd_shared, validate_options, verify_catalog
from llm_probe import request_json_probe


def probe(env):
    base_url = env.get("LLM_BASE_URL", "")
    model = env.get("LLM_MODEL", "")
    validate_options(base_url, model)
    token = env.get("LLM_API_KEY", "")
    if not token:
        raise SetupError("Configure LLM_API_KEY before running this check.")
    if is_amd_shared(base_url):
        verify_catalog(base_url, model, token)
    return request_json_probe(base_url, model, token, timeout=30 if is_amd_shared(base_url) else 20)


def main():
    result = probe(local_env())
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "accepted" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SetupError as exc:
        raise SystemExit(str(exc)) from None
