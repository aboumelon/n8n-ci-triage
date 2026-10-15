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
    # This helper deliberately opts into only provider-labeled free aliases.
    if not re.fullmatch(r"free/[A-Za-z0-9._-]{1,100}", model):
        raise SetupError("Choose a model ID beginning with free/ from the provider's catalog.")


def verify_catalog(base_url: str, model: str, token: str):
    request = urllib.request.Request(
        base_url.rstrip("/") + "/models", headers={"Authorization": "Bearer " + token}
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
        raise SetupError(
            f"Model catalog request failed (HTTP {exc.code}); settings unchanged."
        ) from None
    except (OSError, http.client.HTTPException, ValueError):
        raise SetupError("Cannot read the model catalog; check the key and connectivity.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SetupError("Invalid model catalog; settings were not changed.")
    available = {
        item.get("id")
        for item in payload["data"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if model not in available:
        choices = sorted(
            item for item in available if re.fullmatch(r"free/[A-Za-z0-9._-]{1,100}", item)
        )[:20]
        message = "Selected model is not in your authenticated catalog; settings were not changed."
        if choices:
            message += "\nAvailable free aliases: " + ", ".join(choices)
        raise SetupError(message)


def save_settings(path: Path, base_url: str, model: str, token: str):
    updates = {
        "LLM_ENABLED": "true",
        "LLM_API_KEY": token,
        "LLM_BASE_URL": base_url.rstrip("/"),
        "LLM_MODEL": model,
        "LLM_TIMEOUT_SECONDS": "15",
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
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
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
    verify_catalog(args.base_url, args.model, token)
    save_settings(path, args.base_url, args.model, token)
    print(f"Saved LLM settings for {args.model}. The key was not displayed.")
    print("Catalog access is verified; inference and free quota still need a new CI run.")
    print("Rebuild triage and import/publish the updated n8n workflow; see docs/llm.md.")


if __name__ == "__main__":
    try:
        main()
    except SetupError as exc:
        raise SystemExit(str(exc)) from None
