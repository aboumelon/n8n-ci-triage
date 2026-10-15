"""Send one bounded JSON capability probe without exposing provider content or secrets."""

import http.client
import json
import urllib.error
import urllib.request

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
    "account_not_verified",
    "token_rate_limit_exceeded",
    "ip_rate_limit_exceeded",
    "token_concurrency_rate_limit_exceeded",
    "model_concurrency_rate_limit_exceeded",
    "process_concurrency_rate_limit_exceeded",
    "global_concurrency_rate_limit_exceeded",
    "rate_limit_exceeded",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_payload(response):
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Oversized response")
    return json.loads(raw)


def request_json_probe(base_url: str, model: str, token: str, *, timeout: int) -> dict:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "user", "content": 'Return only the JSON object {"ok":true}.'}
                ],
                "max_tokens": 32,
                "stream": False,
                "response_format": {"type": "json_object"},
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
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            result["http_status"] = response.status
            payload = read_payload(response)
            choice = payload["choices"][0]
            message = choice["message"]
            if not isinstance(message, dict):
                raise ValueError
            content = message.get("content")
            result["content_present"] = isinstance(content, str) and bool(content.strip())
            if (
                not result["content_present"]
                or choice.get("finish_reason") != "stop"
                or message.get("tool_calls")
            ):
                raise ValueError
            answer = json.loads(content)
            valid = isinstance(answer, dict) and set(answer) == {"ok"} and answer["ok"] is True
            result["json_output_valid"] = valid
            result["status"] = "accepted" if valid else "invalid_response"
    except urllib.error.HTTPError as exc:
        result.update(http_status=exc.code, status="rejected")
        if exc.code == 402:
            result["meaning"] = (
                "Provider balance or API-key allowance cannot cover the reservation."
            )
        try:
            payload = read_payload(exc)
            if isinstance(payload, dict) and isinstance(payload.get("detail"), dict):
                payload = payload["detail"]
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            if isinstance(error, dict):
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
