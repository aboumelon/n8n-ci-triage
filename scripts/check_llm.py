"""Probe the configured free model with one tiny request, without exposing credentials."""

import http.client
import json
import urllib.error
import urllib.request

from common import local_env
from configure_llm import NoRedirect, SetupError, validate_options

MAX_RESPONSE_BYTES = 16_384
ERROR_CODES = {
    "insufficient_quota",
    "insufficient_balance",
    "quota_exceeded",
    "budget_exceeded",
    "billing_error",
    "authentication_error",
    "invalid_api_key",
    "permission_denied",
    "rate_limit_error",
    "invalid_request_error",
}


def read_payload(response):
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Oversized response")
    return json.loads(raw)


def probe(env):
    base_url = env.get("LLM_BASE_URL", "")
    model = env.get("LLM_MODEL", "")
    validate_options(base_url, model)
    token = env.get("LLM_API_KEY", "")
    if not token:
        raise SetupError("Configure LLM_API_KEY before running this check.")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": "Reply only OK."}],
                "max_tokens": 32,
                "stream": False,
            }
        ).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "n8n-ci-triage/0.1",
        },
    )
    result = {"model": model, "requested_output_tokens": 32}
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            payload = read_payload(response)
            message = payload["choices"][0]["message"]
            if not isinstance(message, dict):
                raise ValueError
            result.update(
                http_status=response.status,
                status="accepted",
                content_present=bool(str(message.get("content") or "").strip()),
            )
    except urllib.error.HTTPError as exc:
        result.update(http_status=exc.code, status="rejected")
        if exc.code == 402:
            result["meaning"] = (
                "Provider balance or API-key allowance cannot cover the reservation."
            )
        try:
            payload = read_payload(exc)
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            if isinstance(error, dict):
                # Only fixed, known codes can be displayed. Never echo provider messages.
                for field in ("code", "type"):
                    value = error.get(field)
                    if isinstance(value, str) and value in ERROR_CODES and token not in value:
                        result["provider_" + field] = value
        except (OSError, http.client.HTTPException, ValueError, RecursionError):
            pass
    except (OSError, http.client.HTTPException):
        result["status"] = "connection_failed"
    except (ValueError, KeyError, TypeError, IndexError, RecursionError):
        result["status"] = "invalid_response"
    return result


def main():
    result = probe(local_env())
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "accepted" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SetupError as exc:
        raise SystemExit(str(exc)) from None
