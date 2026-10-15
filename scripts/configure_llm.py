"""Verify an OpenAI-compatible free model and save a hidden API key to .env."""

import argparse
import getpass
import http.client
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
import warnings
from pathlib import Path
from urllib.parse import urlsplit

from common import ROOT

DEFAULT_BASE_URL = "https://api.apinex.bond/v1"
DEFAULT_MODEL = "free/deepseek-v4.1-flash"
AMD_BASE_URL = "https://developer.amd.com.cn/radeon/api/v1"
AMD_MODEL = "MiniCPM5-2B"


def is_amd_shared(base_url: str) -> bool:
    return base_url.rstrip("/") == AMD_BASE_URL


class SetupError(Exception):
    """A setup failure whose text is safe to display."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_options(base_url: str, model: str):
    url = urlsplit(base_url)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or not re.fullmatch(r"https://[A-Za-z0-9.:/-]+", base_url)
    ):
        raise SetupError("Use an HTTPS API URL without credentials, a query, or env syntax.")
    # AMD publishes plain model IDs and an explicit free flag in its catalog.
    if is_amd_shared(base_url):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", model):
            raise SetupError("Use an exact model ID from AMD's Public Free Model APIs catalog.")
    elif not re.fullmatch(r"free/[A-Za-z0-9._-]{1,100}", model):
        raise SetupError("Choose a model ID beginning with free/ from the provider's catalog.")


def fetch_catalog(base_url: str, token: str) -> dict:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "User-Agent": "n8n-ci-triage/0.1",
        },
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise SetupError(
                    "Model catalog exceeded the size limit; settings were not changed."
                )
            payload = json.loads(raw)
    except urllib.error.HTTPError as exc:
        message = f"Model catalog request failed (HTTP {exc.code}); settings unchanged."
        # Do not display error bodies: upstreams may echo credentials or request details.
        content_type = exc.headers.get("Content-Type", "").split(";", 1)[0].lower()
        try:
            error_body = exc.read(4_096).lower()
        except (OSError, http.client.HTTPException):
            error_body = b""
        edge_denial = (
            content_type in {"text/html", "text/plain"}
            or b"cloudflare-1xxx-errors/error-1010" in error_body
            or b"error code: 1010" in error_body
        )
        if exc.code == 403 and edge_denial:
            message += (
                " The response indicates a provider edge/web access denial. "
                "Ask the provider whether requests from this VM are allowed."
            )
        elif exc.code in {401, 403}:
            message += (
                " Check the API key and its account/model permissions in the provider dashboard."
            )
        raise SetupError(message) from None
    except (OSError, http.client.HTTPException, ValueError):
        raise SetupError("Cannot read the model catalog; check the key and connectivity.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SetupError("Invalid model catalog; settings were not changed.")
    return {
        item["id"]: item
        for item in payload["data"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def catalog_summary(available: dict, token: str) -> list[dict]:
    def flag(item, name):
        if name not in item:
            return "missing"
        value = item[name]
        if type(value) is bool:
            return value
        return "type:" + type(value).__name__

    result = []
    for name, item in list(available.items())[:50]:
        safe_name = (
            name
            if re.fullmatch(r"[A-Za-z0-9/._-]{1,120}", name) and token not in name
            else "[redacted]"
        )
        row = {"id": safe_name}
        for field in ("free", "json_output", "structured_outputs"):
            row[field] = flag(item, field)
        parameters = item.get("supported_parameters")
        if isinstance(parameters, list):
            row["supported_parameters"] = [
                value
                for value in ("response_format", "max_tokens", "stream")
                if value in parameters
            ]
        providers = item.get("providers")
        if isinstance(providers, list):
            row["provider_flags"] = [
                {field: flag(provider, field) for field in ("free", "json_output")}
                for provider in providers[:3]
                if isinstance(provider, dict)
            ]
        result.append(row)
    return result


def verify_catalog(base_url: str, model: str, token: str):
    available = fetch_catalog(base_url, token)
    if model not in available:
        pattern = (
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}"
            if is_amd_shared(base_url)
            else r"free/[A-Za-z0-9._-]{1,100}"
        )
        choices = sorted(
            name
            for name, item in available.items()
            if re.fullmatch(pattern, name)
            and (
                not is_amd_shared(base_url)
                or (item.get("free") is True and item.get("json_output") is True)
            )
        )[:20]
        message = "Selected model is not in your authenticated catalog; settings were not changed."
        if choices:
            message += "\nAvailable free aliases: " + ", ".join(choices)
        raise SetupError(message)
    if is_amd_shared(base_url) and (
        available[model].get("free") is not True or available[model].get("json_output") is not True
    ):
        raise SetupError(
            "AMD's catalog must mark the selected model free=true and json_output=true; "
            "settings were not changed.\n"
            + json.dumps(catalog_summary({model: available[model]}, token), indent=2)
            + "\nInspect all models: "
            "python3 scripts/configure_llm.py --provider amd --list-models"
        )


def save_settings(path: Path, base_url: str, model: str, token: str):
    updates = {
        "LLM_ENABLED": "true",
        "LLM_API_KEY": token,
        "LLM_BASE_URL": base_url.rstrip("/"),
        "LLM_MODEL": model,
        "LLM_TIMEOUT_SECONDS": "30" if is_amd_shared(base_url) else "15",
        "LLM_MAX_OUTPUT_TOKENS": "1200",
    }
    lines = path.read_text().splitlines()
    seen = set()
    for index, line in enumerate(lines):
        key = line.partition("=")[0].strip()
        if key in updates:
            lines[index] = f"{key}={updates[key]}"
            seen.add(key)
    lines.extend(f"{key}={value}" for key, value in updates.items() if key not in seen)
    fd, staged = tempfile.mkstemp(prefix=".env.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(staged, path)
    finally:
        Path(staged).unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("apinex", "amd"), default="apinex")
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="Print model IDs and capability flags without changing settings or running inference",
    )
    args = parser.parse_args(argv)
    args.base_url = args.base_url or (AMD_BASE_URL if args.provider == "amd" else DEFAULT_BASE_URL)
    args.model = args.model or (AMD_MODEL if args.provider == "amd" else DEFAULT_MODEL)
    validate_options(args.base_url, args.model)
    path = ROOT / ".env"
    if not path.is_file():
        raise SetupError("Create .env before configuring the LLM.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass("LLM API key (hidden): ").strip()
    except (getpass.GetPassWarning, EOFError):
        raise SetupError("Use an interactive terminal so the key remains hidden.") from None
    if not re.fullmatch(r"[A-Za-z0-9._-]{16,256}", token):
        raise SetupError("Invalid API key format; settings were not changed.")
    if args.list_models:
        print(
            json.dumps(
                {"models": catalog_summary(fetch_catalog(args.base_url, token), token)}, indent=2
            )
        )
        return
    verify_catalog(args.base_url, args.model, token)
    save_settings(path, args.base_url, args.model, token)
    print(f"Saved LLM settings for {args.model}. The key was not displayed.")
    print("Catalog access is verified. Next: python3 scripts/check_llm.py, then recreate triage.")
    print("For first-time LLM setup, also import/publish the n8n workflow; see docs/llm.md.")


if __name__ == "__main__":
    try:
        main()
    except SetupError as exc:
        raise SystemExit(str(exc)) from None
